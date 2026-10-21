# -*- coding: utf-8 -*-
"""AI 修改代码：让模型**给出修正后的完整源码**，而不是只给讲解。

和「讲解」的区别，以及必须守住的边界：

    explain（已有）: 只讲为什么错、怎么改，**绝不输出可复制代码**
    fix（本模块）  : 允许输出完整修正代码，但**只进编辑器缓冲区，永不自动存盘**

为什么这么设计：
    这个工具最初的原则是"不替你改代码"。但学生真正卡住的时候，
    只看文字说明仍然可能不会写。所以加一条**显式触发**的修正通道：
      · 必须点按钮才发生，不会自己改
      · 改动一律进编辑器，标签会带 * 号，退出时会问要不要存
      · 三种对照方式让用户先看清改了什么，再决定要不要
      · 永远保留"还原"用的原文

提示词约束（教模型只做最小修正，别自作主张重写算法）：
    1. fixed_code 必须是完整文件，不许用 ... 省略
    2. 只修到"能编译通过"，不改算法、不改程序本意
    3. 保留原有中文注释与缩进
    4. changes 里的行号用输入代码的行号，方便做行内对照
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from . import guard, prompt
from .ai import AIError, DeepSeekClient
from .diagnostic import Diagnostic

# 稳定前缀：和 prompt.STABLE_PREFIX 一样必须逐字节不变，才能吃到上下文缓存
FIX_PREFIX = """你是面向中文初学者的 C 语言代码修正助手。学生写的 C 程序编译不通过，你要给出修正后的代码。

输出必须是 json 对象，字段如下：
{
  "fixed_code": "修正后的完整源文件内容",
  "changes": [
    {"line": 4, "before": "原写法里的一小段", "after": "改成什么", "why": "一句话说明原因"}
  ],
  "confidence": "high 或 medium 或 low",
  "refuse_reason": "如果信息不足无法修正，在这里说明还缺什么；否则填空字符串"
}

硬性规则（必须遵守）：
1. fixed_code 必须是**完整可编译的源文件**：从第一行到最后一行都在，
   绝对不要用 ... 或「其余不变」之类的话省略任何部分。
2. 只做**让代码能编译通过**的最小修改。不要重写学生的算法，
   不要改变程序原本想做的事情，不要顺手"优化"或"重构"。
3. 保留原有的中文注释、缩进风格和空行，能不动的地方一律不动。
4. changes 里每一项的 line 用**输入代码中的行号**（从 1 开始数）。
   before / after 都只写该行涉及的一小段，不要贴整行整段的代码。
5. 如果是"漏了分号""括号不配对""全角标点""变量没声明"这类小问题，
   只改那一处，其余行原样返回。
6. 不要编造。无法判断时把 confidence 设为 "low"，并在 refuse_reason 里说明。

示例输入：
编译器：msvc
错误码：C2146
编译器原文：语法错误: 缺少";"(在标识符"printf"的前面)
完整代码：
1  #include <stdio.h>
2  int main(void) {
3      int x = 10
4      printf("%d\\n", x);
5      return 0;
6  }

示例输出（json）：
{"fixed_code":"#include <stdio.h>\\nint main(void) {\\n    int x = 10;\\n    printf(\\"%d\\\\n\\", x);\\n    return 0;\\n}\\n","changes":[{"line":3,"before":"int x = 10","after":"int x = 10;","why":"语句末尾缺少分号"}],"confidence":"high","refuse_reason":""}
"""

STRICTER_SUFFIX = ("\n\n上一次的输出不是合法的 json 对象，或者 fixed_code 不是一个完整程序。"
                   "请只输出一个 json 对象，不要任何解释文字、不要 markdown 代码块。")


class FixResult:
    def __init__(self, ok=False, fixed_code="", changes=None, confidence="",
                 degraded=False, notes=None, warnings=None, usage=None,
                 attempts=0, error="", refuse_reason=""):
        self.ok = ok
        self.fixed_code = fixed_code
        self.changes: List[Dict[str, Any]] = changes or []
        self.confidence = confidence
        self.degraded = degraded
        self.notes: List[str] = notes or []
        self.warnings: List[str] = warnings or []
        self.usage: Dict[str, Any] = usage or {}
        self.attempts = attempts
        self.error = error
        self.refuse_reason = refuse_reason

    def summary(self) -> str:
        if not self.ok:
            return "失败：%s" % self.error
        bits = ["%d 处修改" % len(self.changes)]
        if self.confidence:
            bits.append("把握：%s" % self.confidence)
        if self.usage:
            bits.append("tokens in=%s out=%s" % (self.usage.get("prompt_tokens", "?"),
                                                 self.usage.get("completion_tokens", "?")))
        if self.degraded:
            bits.append("已降级")
        return "，".join(bits)


def _strip_fences(text: str) -> str:
    """去掉模型偶尔带上的 ```c 围栏。"""
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if len(lines) > 1:
            s = "\n".join(lines[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip("\n")


def _norm(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def looks_like_full_source(original: str, fixed: str) -> Tuple[bool, str]:
    """判断模型返回的是不是"完整程序"。

    这一步很重要：模型很爱偷懒说"其余部分不变"，或者只返回改动的几行。
    如果不拦住，学生把这种半截代码粘回去，本来能编译的程序反而废了。
    """
    fixed = _norm(fixed).strip()
    if not fixed:
        return False, "返回内容为空"
    if re.search(r"^\s*(\.\.\.|…)\s*$", fixed, re.M):
        return False, "返回内容里有省略号占位"
    if len(fixed) < 20:
        return False, "返回内容过短，不像完整文件"
    # 代码量不应该和原文差太多。模型重写整份程序时这里会拦下来。
    lo = max(20, int(len(_norm(original)) * 0.35))
    hi = int(max(len(_norm(original)), 200) * 2.5)
    if len(fixed) < lo:
        return False, "返回内容比原文短太多（%d < %d 字符），不像完整文件" % (len(fixed), lo)
    if len(fixed) > hi:
        return False, "返回内容比原文长太多（%d > %d 字符）" % (len(fixed), hi)
    return True, ""


def _clean_changes(raw: Any, total_lines: int) -> List[Dict[str, Any]]:
    """把模型给的 changes 清洗成安全的结构（行号必须落在合法范围内）。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for it in raw[:12]:
        if not isinstance(it, dict):
            continue
        try:
            line = int(it.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        if line < 0 or line > total_lines + 1:
            line = 0
        why = str(it.get("why") or "").strip()
        before = str(it.get("before") or "").strip()
        after = str(it.get("after") or "").strip()
        if not why and not after:
            continue
        out.append({"line": line, "before": before[:120], "after": after[:160],
                    "why": why[:160]})
    return out


def build_messages(diag: Diagnostic, full_code: str,
                   code_context: str = "") -> List[Dict[str, str]]:
    """稳定前缀在前，变化的代码在后（缓存友好）。"""
    body = [
        "编译器：%s" % diag.compiler,
        "错误码：%s" % (diag.code or "（无错误码）"),
        "级别：%s" % diag.level,
        "编译器原文：%s" % diag.message,
    ]
    if diag.line:
        body.append("报错行号：%d（仅供定位，不要在回答里提行号之外的说明）" % diag.line)
    if code_context:
        body.append("出错附近的代码（--> 标出错行）：")
        body.append(code_context)
    body.append("完整代码（行号仅用于对照，fixed_code 里不要带行号）：")
    for i, ln in enumerate(_norm(full_code).split("\n"), 1):
        body.append("%4d  %s" % (i, ln))
    body.append("请按前面的 json 格式给出修正后的完整代码。")
    return [
        {"role": "system", "content": FIX_PREFIX},
        {"role": "user", "content": "\n".join(body)},
    ]


def request_fix(diag: Diagnostic, full_code: str, code_context: str = "",
                client: Optional[DeepSeekClient] = None,
                max_retry: int = 1) -> FixResult:
    """调用模型要一份修正后的完整代码。"""
    if client is None:
        try:
            client = DeepSeekClient()
        except AIError as e:
            return FixResult(ok=False, error=str(e))

    ctx = guard.redact(code_context)          # 脱敏后再发
    messages = build_messages(diag, full_code, ctx)
    warnings: List[str] = []
    usage: Dict[str, Any] = {}
    attempts = 0
    total_lines = _norm(full_code).count("\n") + 1

    for attempt in range(max_retry + 1):
        attempts += 1
        try:
            text, usage = client.chat(messages, max_tokens=4000)
        except AIError as e:
            return FixResult(ok=False, error=str(e), attempts=attempts,
                             warnings=warnings, usage=usage)
        try:
            obj = prompt.parse_json_payload(text)
        except ValueError as e:
            warnings.append("第 %d 次输出不是合法 json（%s）" % (attempt + 1, e))
            if attempt < max_retry:
                messages = messages + [{"role": "user", "content": STRICTER_SUFFIX.strip()}]
                continue
            return FixResult(ok=False, error="模型返回的不是 json", attempts=attempts,
                             warnings=warnings, usage=usage)

        fixed = _strip_fences(str(obj.get("fixed_code") or ""))
        ok, why = looks_like_full_source(full_code, fixed)
        if not ok:
            warnings.append("第 %d 次返回的代码不像完整文件：%s" % (attempt + 1, why))
            if attempt < max_retry:
                messages = messages + [{"role": "user", "content": STRICTER_SUFFIX.strip()}]
                continue
            return FixResult(ok=False, error="模型没给出完整代码（%s）" % why,
                             attempts=attempts, warnings=warnings, usage=usage)

        confidence = str(obj.get("confidence") or "").strip().lower()
        refuse = str(obj.get("refuse_reason") or "").strip()
        if confidence == "low" or refuse:
            # 模型自己没把握：不要给一份可能改坏的代码，退化成"只给建议"
            return FixResult(ok=False, error="模型没把握改这份代码：%s" % (refuse or "confidence=low"),
                             attempts=attempts, warnings=warnings, usage=usage,
                             refuse_reason=refuse)
        return FixResult(ok=True, fixed_code=fixed,
                         changes=_clean_changes(obj.get("changes"), total_lines),
                         confidence=confidence, warnings=warnings, usage=usage,
                         attempts=attempts)

    return FixResult(ok=False, error="重试次数用尽", attempts=attempts,
                     warnings=warnings, usage=usage)


# --------------------------------------------------------------------------
# 行内对照：把"改什么"以注释的形式插在出问题的那一行下面
# --------------------------------------------------------------------------
def _line_end_states(text: str) -> List[str]:
    """每行**行尾**所处的词法状态，用来避免把注释插进多行字符串里。

    复用 engine.format 里的跨行状态机：第 i 行行尾的状态 == 第 i+1 行行首的状态。
    """
    from .format import _scan
    infos = _scan(text)
    states = [info.start_state for info in infos]
    states = states[1:] + ["NORMAL"] if states else []
    return states


def apply_inline_hints(source: str, changes: List[Dict[str, Any]],
                       anchor_line: int = 0, max_hints: int = 6) -> Tuple[str, int]:
    """在相关行下面插入 `// AI 建议：……` 注释行。

    返回 (新文本, 插入条数)。

    两个安全点：
      · 从下往上插，避免插一行之后下面的行号全变了；
      · 如果那一行行尾还在**多行字符串/块注释里**，就跳过——
        往字符串里塞 `//` 会直接改变程序行为。
    """
    src = _norm(source)
    lines = src.split("\n")
    ends = _line_end_states(src)
    n = len(lines)

    picks: List[Tuple[int, str]] = []
    seen = set()
    for ch in changes or []:
        line = int(ch.get("line") or 0)
        if line <= 0 or line > n:
            line = anchor_line or 0
        if line <= 0 or line > n or line in seen:
            continue
        why = (ch.get("why") or "").strip()
        after = (ch.get("after") or "").strip()
        before = (ch.get("before") or "").strip()
        if not why and not after:
            continue
        parts = []
        if before and after:
            parts.append("%s → %s" % (before, after))
        elif after:
            parts.append("改成 %s" % after)
        if why:
            parts.append(why)
        hint = "// AI 建议：%s" % "；".join(parts)
        seen.add(line)
        picks.append((line, hint))
        if len(picks) >= max_hints:
            break

    if not picks:
        return source, 0

    # 行尾处在字符串/块注释里的行不动
    picks = [(ln, h) for ln, h in picks
             if ln - 1 < len(ends) and ends[ln - 1] == "NORMAL"]
    if not picks:
        return source, 0

    picks.sort(key=lambda x: x[0], reverse=True)      # 从下往上插
    for ln, hint in picks:
        lines.insert(ln, "    " + hint)
    return "\n".join(lines), len(picks)


# --------------------------------------------------------------------------
# 对照文本（给"对照"对话框用）
# --------------------------------------------------------------------------
def diff_lines(old: str, new: str) -> List[Tuple[str, str]]:
    """返回 [(标记, 文本)]，标记为 'same' / 'add' / 'del'。

    用 difflib 做行级比较，够用且零依赖。
    """
    import difflib
    a = _norm(old).split("\n")
    b = _norm(new).split("\n")
    out: List[Tuple[str, str]] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                out.append(("same", a[k]))
        elif tag == "delete":
            for k in range(i1, i2):
                out.append(("del", a[k]))
        elif tag == "insert":
            for k in range(j1, j2):
                out.append(("add", b[k]))
        else:                                    # replace
            for k in range(i1, i2):
                out.append(("del", a[k]))
            for k in range(j1, j2):
                out.append(("add", b[k]))
    return out


def diff_stats(old: str, new: str) -> Tuple[int, int]:
    """返回 (新增行数, 删除行数)。"""
    rows = diff_lines(old, new)
    return (sum(1 for t, _ in rows if t == "add"),
            sum(1 for t, _ in rows if t == "del"))
