# -*- coding: utf-8 -*-
"""提示词构造。

关键工程约束（来自 DeepSeek 官方定价页）：
    上下文缓存命中价 $0.003/M，未命中 $0.15/M——**相差 50 倍**。
DeepSeek 的缓存按「前缀匹配」生效，因此系统提示 + 输出规范 + 少样本示例必须是
**逐字节稳定**的前缀，且永远排在最前面；所有会变化的内容（报错、学生代码）放最后。

本模块把这条约束固化成结构：STABLE_PREFIX 是常量，build_messages() 只在其后追加。
tests/test_ai.py 会断言前缀的 sha256 不随输入变化，防止后续改动无意破坏缓存。
"""
import hashlib
import json
from typing import Any, Dict, List

from .diagnostic import Diagnostic

# --------------------------------------------------------------------------
# 稳定前缀：绝不能包含任何随调用变化的内容
# --------------------------------------------------------------------------
STABLE_PREFIX = """你是面向中文初学者的 C 语言报错讲解助手。学生刚学 C，看不懂编译器的报错。

你的任务：根据编译器给出的错误信息与相关代码，用简体中文写一段讲解，并按 json 格式输出。

输出必须是 json 对象，字段如下：
{
  "one_line": "一句话说明这是什么问题（不超过 40 字，不要出现行号）",
  "why": ["为什么会这样的原因，1-3 条，每条一句话"],
  "how_to_fix": ["具体怎么改，1-3 条，每条一句话"],
  "pitfalls": ["初学者容易一起踩的坑，0-2 条"],
  "concept": "顺带值得记住的一个知识点，一句话",
  "confidence": "high 或 medium 或 low",
  "refuse_reason": "如果信息不足无法判断，在这里说明还缺什么；否则填空字符串"
}

硬性规则（必须遵守）：
1. 不要输出完整的可复制代码。可以说「把第 N 行末尾的分号补上」「把 = 改成 ==」这类具体动作，
   但绝对不要给出整段程序或多个语句组成的代码块。
2. 不要提供行号、列号或文件名。位置信息由编译器决定，你既不需要也不允许给出位置。
3. 不要编造。如果仅凭给出的信息无法确定原因，把 confidence 设为 "low"，
   并在 refuse_reason 里写明还需要看到什么。
4. 用初学者能懂的说法，避免「左值」「符号解析」「翻译单元」这类术语；
   必须使用术语时，紧跟一句大白话解释。
5. 不要重复编译器原文，说人话。

示例输入：
编译器：msvc
错误码：C2146
编译器原文：语法错误: 缺少";"(在标识符"printf"的前面)
出错行：    printf("%d\\n", x);
上一行：    int x = 10

示例输出（json）：
{"one_line":"上一行的语句没有用分号结尾。","why":["C 语言要求每条语句以分号结束。","编译器是读到下一行才发现上一行没写完，所以报错位置常常指向下一行。"],"how_to_fix":["看报错行的上一行末尾，补一个英文分号。","注意必须是英文半角分号，中文的全角分号不算。"],"pitfalls":["宏定义 #define 的行末尾不需要加分号。"],"concept":"分号是 C 语言的语句结束符。","confidence":"high","refuse_reason":""}
"""

# 二次提醒：JSON 校验失败时追加（同样保持稳定，便于复用缓存前缀）
STRICTER_SUFFIX = """\n\n上一次的输出不是合法的 json 对象。请只输出一个 json 对象，不要任何解释文字、不要 markdown 代码块。"""


def stable_prefix_sha() -> str:
    """用于测试前缀的字节稳定性（缓存友好性的前提）。"""
    return hashlib.sha256(STABLE_PREFIX.encode("utf-8")).hexdigest()


def build_messages(diag: Diagnostic, code_context: str = "",
                   compiler_name: str = "") -> List[Dict[str, str]]:
    """构造 messages。稳定前缀在前，可变内容全部在最后一条消息里。"""
    variable = [
        "编译器：%s" % (compiler_name or diag.compiler),
        "错误码：%s" % (diag.code or "（无，GCC 语法错误通常没有错误码）"),
        "级别：%s" % diag.level,
        "编译器原文：%s" % diag.message,
    ]
    if diag.line:
        variable.append("出错行号（仅供你定位上下文，不要写进回答）：%d" % diag.line)
    if code_context:
        variable.append("相关代码（出错行已用 --> 标出）：")
        variable.append(code_context)
    variable.append("请按前面的 json 格式给出讲解。")

    return [
        {"role": "system", "content": STABLE_PREFIX},
        {"role": "user", "content": "\n".join(variable)},
    ]


def build_retry_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """校验失败后的重试消息：保留原前缀，仅追加提醒（前缀不变，仍可命中缓存）。"""
    out = list(messages)
    out.append({"role": "user", "content": STRICTER_SUFFIX.strip()})
    return out


def parse_json_payload(text: str) -> Dict[str, Any]:
    """从模型输出里取出 json 对象。JSON 模式仍可能包一层 markdown 围栏，需要容错。"""
    if not text:
        raise ValueError("空输出")
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    try:
        obj = json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            raise
        obj = json.loads(s[i:j + 1])
    if not isinstance(obj, dict):
        raise ValueError("顶层不是 json 对象")
    return obj
