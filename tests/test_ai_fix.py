#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 修改代码的离线测试。

这个功能会**改动学生正在写的文件**，所以测试重点不是"能不能调通模型"，
而是**改坏了能不能被拦住**：

1. 模型偷懒（返回 `...` 省略、只返回改动几行）必须被识别出来并拒绝 ——
   把半截代码粘回去，本来能编译的程序反而会废掉。
2. 模型说"没把握"时不许给代码，只能给建议。
3. 行内对照的注释**绝不能插进多行字符串里**——那等于改了程序输出。
4. 行号超出范围、字段类型乱写，都不能让程序崩。
5. 一律只进缓冲区：本模块不提供任何写文件的入口。

全部用假客户端，不联网、不花钱。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import ai_fix                                     # noqa: E402
from engine.diagnostic import Diagnostic                      # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         "" if cond else "　→ " + str(detail)))


class FakeClient:
    """假客户端：按脚本回复，并记录收到的 messages。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0) if self.replies else "{}"
        if isinstance(reply, Exception):
            raise reply
        return reply, {"prompt_tokens": 300, "completion_tokens": 200}


SOURCE = (
    '#include <stdio.h>\n'
    'int main(void) {\n'
    '    int x = 10\n'
    '    printf("%d\\n", x);\n'
    '    return 0;\n'
    '}\n'
)

GOOD_JSON = (
    '{"fixed_code":"#include <stdio.h>\\n'
    'int main(void) {\\n'
    '    int x = 10;\\n'
    '    printf(\\"%d\\\\n\\", x);\\n'
    '    return 0;\\n'
    '}\\n",'
    '"changes":[{"line":3,"before":"int x = 10","after":"int x = 10;",'
    '"why":"语句末尾缺少分号"}],'
    '"confidence":"high","refuse_reason":""}'
)


def diag(line=3):
    return Diagnostic(compiler="msvc", message='语法错误: 缺少";"', code="C2146",
                      level="error", file="a.c", line=line)


# ---------------------------------------------------------------- 1. 完整代码判定
print("— 判断模型返回的是不是完整文件 —")
ok, why = ai_fix.looks_like_full_source(SOURCE, SOURCE.replace("int x = 10",
                                                               "int x = 10;"))
check("完整改写 → 通过", ok, why)

ok, why = ai_fix.looks_like_full_source(SOURCE, "int x = 10;\n    printf(...);")
check("带 ... 省略 → 拒绝", not ok, why)

ok, why = ai_fix.looks_like_full_source(SOURCE, "    int x = 10;")
check("只返回一行改动 → 拒绝（否则粘回去就废了）", not ok, why)

ok, why = ai_fix.looks_like_full_source(SOURCE, "")
check("空内容 → 拒绝", not ok, why)

ok, why = ai_fix.looks_like_full_source(SOURCE, "\n".join(["int a;"] * 200))
check("异常膨胀的内容 → 拒绝", not ok, why)

# ---------------------------------------------------------------- 2. 正常调用
print()
print("— 正常调用 —")
client = FakeClient([GOOD_JSON])
res = ai_fix.request_fix(diag(), SOURCE, client=client)
check("调用成功且只尝试一次", res.ok and res.attempts == 1, res.summary())
check("拿回了修正后的代码", "int x = 10;" in res.fixed_code, res.fixed_code)
check("改动列表有 1 条且行号正确",
      len(res.changes) == 1 and res.changes[0]["line"] == 3, res.changes)
check("记录 token 用量", res.usage.get("prompt_tokens") == 300, res.usage)

# 提示词里必须是"稳定前缀 + 完整代码"
sent = client.calls[0]
check("第一条是 system 稳定前缀", sent[0]["role"] == "system"
      and sent[0]["content"] == ai_fix.FIX_PREFIX, sent[0]["role"])
check("用户消息里带上了完整代码", "完整代码" in sent[1]["content"], sent[1]["content"][:120])
check("用户消息里带上了编译器原文", "C2146" in sent[1]["content"], sent[1]["content"][:120])

# ---------------------------------------------------------------- 3. 拒绝与重试
print()
print("— 模型偷懒 / 没把握 —")
client = FakeClient(['{"fixed_code":"int x = 10;","changes":[],"confidence":"high"}'])
res = ai_fix.request_fix(diag(), SOURCE, client=client, max_retry=0)
check("只返回半截代码 → 失败而不是硬塞", not res.ok, res.error)
check("失败原因说清楚是「不像完整文件」", "完整" in res.error or "短" in res.error, res.error)

client = FakeClient(['这不是 json', GOOD_JSON])
res = ai_fix.request_fix(diag(), SOURCE, client=client, max_retry=1)
check("首次非 JSON → 重试后成功", res.ok and res.attempts == 2, res.summary())

LOW = ('{"fixed_code":"%s","changes":[],"confidence":"low",'
       '"refuse_reason":"看不到头文件上下文"}') % SOURCE.replace('"', '\\"').replace("\n", "\\n")
client = FakeClient([LOW])
res = ai_fix.request_fix(diag(), SOURCE, client=client)
check("模型说没把握 → 不给代码", not res.ok, res.summary())
check("没把握的原因被带回来", "头文件" in (res.refuse_reason or ""), res.refuse_reason)

from engine.ai import AIError                                 # noqa: E402
client = FakeClient([AIError("网络不可达")])
res = ai_fix.request_fix(diag(), SOURCE, client=client)
check("网络异常 → 干净失败，不抛异常", not res.ok and "网络" in res.error, res.error)

# ---------------------------------------------------------------- 4. changes 清洗
print()
print("— 改动列表清洗 —")
raw = [
    {"line": 3, "before": "a", "after": "b", "why": "正常"},
    {"line": 999, "before": "", "after": "x", "why": "行号越界"},
    {"line": "abc", "before": "", "after": "y", "why": "行号不是数字"},
    "不是字典",
    {"line": 2, "before": "", "after": "", "why": ""},
]
cleaned = ai_fix._clean_changes(raw, 6)
check("越界行号被归零而不是丢弃条目", any(c["line"] == 0 for c in cleaned), cleaned)
check("非数字行号归零", any(c["line"] == 0 and c["after"] == "y" for c in cleaned), cleaned)
check("非字典项被跳过", len(cleaned) == 3, len(cleaned))
check("没有内容的条目被跳过", not any(c["why"] == "" and c["after"] == "" for c in cleaned),
      cleaned)

# ---------------------------------------------------------------- 5. 行内对照
print()
print("— 行内对照（// AI 建议）—")
changes = [{"line": 3, "before": "int x = 10", "after": "int x = 10;", "why": "缺分号"}]
new_src, n = ai_fix.apply_inline_hints(SOURCE, changes)
check("插入了 1 条注释", n == 1, n)
check("注释插在第 3 行下面",
      new_src.split("\n")[3].strip().startswith("// AI 建议"), new_src.split("\n")[3])
check("原来的代码一行没被改动",
      [ln for ln in new_src.split("\n") if not ln.strip().startswith("// AI 建议")]
      == SOURCE.split("\n"), "代码行有变化")
check("插入后仍然是合法文本（行数 +1）",
      len(new_src.split("\n")) == len(SOURCE.split("\n")) + 1, new_src)

# 多行字符串：绝不能往里插注释
STR_SRC = (
    '#include <stdio.h>\n'
    'int main(void) {\n'
    '    printf("第一行\n'
    '第二行\n'
    '第三行");\n'
    '    return 0;\n'
    '}\n'
)
new2, n2 = ai_fix.apply_inline_hints(STR_SRC, [{"line": 4, "after": "x", "why": "测试"}])
check("多行字符串内部的行不插注释（插了就是改程序输出）", n2 == 0, (n2, new2))

# 多行块注释同理
CMT_SRC = (
    '/*\n'
    ' * 说明\n'
    ' */\n'
    'int main(void) { return 0; }\n'
)
new3, n3 = ai_fix.apply_inline_hints(CMT_SRC, [{"line": 2, "after": "x", "why": "测试"}])
check("块注释内部不插注释", n3 == 0, (n3, new3))

# 从下往上插，行号不串
multi = [
    {"line": 2, "after": "A", "why": "第二条"},
    {"line": 4, "after": "B", "why": "第一条"},
]
new4, n4 = ai_fix.apply_inline_hints(SOURCE, multi)
lines4 = new4.split("\n")
check("多行插入条数正确", n4 == 2, n4)
check("先改的行后面才插，行号不会错位",
      lines4[2].strip().startswith("// AI 建议") and lines4[5].strip().startswith("// AI 建议"),
      lines4[:7])

# 行号非法时回落到报错行
new5, n5 = ai_fix.apply_inline_hints(SOURCE, [{"line": 0, "after": "z", "why": "回落到报错行"}],
                                     anchor_line=3)
check("没有行号时回落到报错行", n5 == 1 and new5.split("\n")[3].strip().startswith("// AI 建议"),
      new5.split("\n")[3])

# ---------------------------------------------------------------- 6. 对照
print()
print("— 对照数据 —")
rows = ai_fix.diff_lines("a\nb\nc\n", "a\nB\nc\nd\n")
tags = [t for t, _ in rows]
check("对照能标出删除", "del" in tags, tags)
check("对照能标出新增", "add" in tags, tags)
check("对照能标出未变行", "same" in tags, tags)
add, dele = ai_fix.diff_stats("a\nb\n", "a\nB\nc\n")
check("统计新增/删除行数", add == 2 and dele == 1, (add, dele))

# ---------------------------------------------------------------- 7. 安全性
print()
print("— 安全边界 —")
check("本模块没有任何写文件的函数（只产文本，存不存由界面决定）",
      not any(n in dir(ai_fix) for n in ("write_file", "save_file", "apply_to_file")),
      [n for n in dir(ai_fix) if "write" in n.lower() or "save" in n.lower()])

# ---------------------------------------------------------------- 汇总
print()
failed = [r for r in results if not r[1]]
print("AI 修改单测：%d 项，通过 %d，失败 %d"
      % (len(results), len(results) - len(failed), len(failed)))
for name, _, detail in failed:
    print("  失败：%s　→ %s" % (name, detail))
sys.exit(1 if failed else 0)
