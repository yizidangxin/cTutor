# -*- coding: utf-8 -*-
"""文本回退解析。

为什么需要它（实测依据）：
- **MSVC 的链接器诊断不进 SARIF**：main 拼成 mian 时 SARIF 的 results 为空数组，
  只有一行文本 "LINK : fatal error LNK1561: 必须定义入口点"。链接错误对初学者极其常见
  （拼错 main、少写 .c 文件、函数名拼错），不处理会漏掉一整类问题。
- GCC 的 fix-it 提示（-fdiagnostics-parseable-fixits）是文本格式。

除此之外不解析编译器散文——编译诊断一律走 SARIF，避免正则脆弱性。
"""
import re
from typing import List

from .diagnostic import Diagnostic, FixIt

# MSVC:  C:\a\b.c(5): error C2146: 语法错误: 缺少“;”(在标识符“printf”的前面)
# MSVC:  C:\a\b.c(5,7): warning C4477: ...
_MSVC_DIAG = re.compile(
    r"^(?P<file>[^()]+?)\((?P<line>\d+)(?:,(?P<col>\d+))?\)\s*:\s*"
    r"(?P<level>fatal error|error|warning|note)\s+"
    r"(?P<code>[A-Z]{1,4}\d{3,5})\s*:\s*(?P<msg>.*)$"
)
# MSVC 链接器:  LINK : fatal error LNK1561: 必须定义入口点
_MSVC_LINK = re.compile(
    r"^(?P<tool>LINK|cl|lib|mt)\s*:\s*(?P<level>fatal error|error|warning)\s+"
    r"(?P<code>LNK\d{4}|D\d{4})\s*:\s*(?P<msg>.*)$"
)
# GCC/Clang:  a.c:5:7: error: expected ';' before '}' token [-Werror=...]
_GCC_DIAG = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*"
    r"(?P<level>fatal error|error|warning|note)\s*:\s*(?P<msg>.*?)"
    r"(?:\s*\[(?P<opt>-W[a-zA-Z0-9=_-]+)\])?$"
)
# GCC fix-it:  fix-it:"a.c":{45:3-45:21}:"gtk_widget_show_all"
_GCC_FIXIT = re.compile(
    r'^fix-it:"(?P<file>[^"]+)":\{(?P<sl>\d+):(?P<sc>\d+)-(?P<el>\d+):(?P<ec>\d+)\}:"'
    r'(?P<rep>(?:[^"\\]|\\.)*)"$'
)

_UNESCAPE = {"\\\\": "\\", "\\t": "\t", "\\n": "\n", '\\"': '"'}


def _unescape(s: str) -> str:
    for k, v in _UNESCAPE.items():
        s = s.replace(k, v)
    return re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_msvc_text(text: str) -> List[Diagnostic]:
    out: List[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _MSVC_LINK.match(line)
        if m:
            out.append(Diagnostic(
                compiler="msvc", message=m.group("msg"), level=m.group("level"),
                code=m.group("code"), file="", line=0, column=0,
                origin="text", raw=line))
            continue
        m = _MSVC_DIAG.match(line)
        if m:
            out.append(Diagnostic(
                compiler="msvc", message=m.group("msg"), level=m.group("level"),
                code=m.group("code"), file=m.group("file"),
                line=int(m.group("line")), column=int(m.group("col") or 0),
                origin="text", raw=line))
    return out


def parse_gcc_text(text: str) -> "tuple[List[Diagnostic], List[FixIt]]":
    diags: List[Diagnostic] = []
    fixits: List[FixIt] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _GCC_FIXIT.match(line)
        if m:
            fixits.append(FixIt(
                file=m.group("file"),
                start_line=int(m.group("sl")), start_col=int(m.group("sc")),
                end_line=int(m.group("el")), end_col=int(m.group("ec")),
                replacement=_unescape(m.group("rep"))))
            continue
        m = _GCC_DIAG.match(line)
        if m:
            diags.append(Diagnostic(
                compiler="gcc", message=m.group("msg"), level=m.group("level"),
                code=(m.group("opt") or ""), file=m.group("file"),
                line=int(m.group("line")), column=int(m.group("col")),
                origin="text", raw=line))
    return diags, fixits


# GCC 链接错误：ld.exe: xxx.o:(.text+0x0): undefined reference to `main'
# 这类消息**没有 行:列**，所以上面的 _GCC_DIAG 匹配不到——实测中表现为
# "main 拼错时 GCC 返回码是 1，但一条诊断都抓不到"。初学者极常见，必须处理。
_GCC_LINK_MSG = re.compile(
    r"(?P<msg>(?:undefined reference to|multiple definition of)\s+[`'\"](?P<sym>[^`'\"]+)[`'\"])"
)
_GCC_LINK_SKIP = re.compile(r"ld returned \d+ exit status|collect2\.exe")


def parse_gcc_link_errors(text: str) -> List[Diagnostic]:
    """从 GCC/ld 的链接输出里提取可读的链接错误（无位置信息，与 MSVC 的 LNK 情形一致）。"""
    out: List[Diagnostic] = []
    seen = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _GCC_LINK_SKIP.search(line):
            continue
        m = _GCC_LINK_MSG.search(line)
        if not m:
            continue
        msg = m.group("msg")
        if msg in seen:
            continue
        seen.add(msg)
        out.append(Diagnostic(compiler="gcc", message=msg, level="error",
                              code="", file="", line=0, column=0,
                              origin="text", raw=line))
    return out


def parse_full_text(text: str, compiler: str):
    """SARIF 不可用时的整体回退解析。

    评审建议点名要先验证「SARIF 版本兼容 fallback」：当 SARIF 文件缺失、损坏或版本
    不认识时，必须退回纯文本解析，功能不能中断。代价是失去结构化字段，但好过没有输出。
    """
    if compiler == "msvc":
        return parse_msvc_text(text), []
    diags, fixits = parse_gcc_text(text)
    diags += parse_gcc_link_errors(text)
    return diags, fixits


def merge_text_fallback(sarif_diags: List[Diagnostic], text: str,
                        compiler: str) -> List[Diagnostic]:
    """SARIF 为主，文本负责两件 SARIF 做不到的事：

    1. **回填缺失的 level**。实测 MSVC 的 SARIF 对警告省略 level 字段，只对错误写
       "error"；若默认成 error，会把警告当错误报给初学者。文本输出里有明确的
       warning/error 字样，是 level 的权威来源。
    2. **补充链接错误**。LNK1561 / LNK2019 等链接器诊断完全不进 SARIF。
    """
    if compiler == "msvc":
        text_diags = parse_msvc_text(text)
    else:
        text_diags, _ = parse_gcc_text(text)
        text_diags += parse_gcc_link_errors(text)

    # --- 1) 用文本里的 level 回填 ---
    by_code, by_code_line = {}, {}
    for d in text_diags:
        if d.level:
            by_code.setdefault(d.code, set()).add(d.level)
            by_code_line[(d.code, d.line)] = d.level
    for d in sarif_diags:
        if not d.level:
            lvl = by_code_line.get((d.code, d.line))
            if not lvl:
                cand = by_code.get(d.code)
                # 同一个错误码在不同场景下可能级别不同，无法唯一确定时保守取 warning
                lvl = sorted(cand)[0] if cand and len(cand) == 1 else "warning"
            d.level = lvl

    # --- 2) 补充 SARIF 未覆盖的诊断 ---
    have = {(d.code, d.line, d.file) for d in sarif_diags}
    extra = []
    for d in text_diags:
        if (d.code, d.line, d.file) not in have:
            extra.append(d)
            have.add((d.code, d.line, d.file))
    return sarif_diags + extra
