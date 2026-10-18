# -*- coding: utf-8 -*-
"""代码对齐（缩进格式化）——纯确定性，可离线、可单测。

设计原则（和本项目其它部分一致）：
    **只动空白，不动代码。**

具体说：只改每行的「行首缩进」和「行尾空格」，任何非空白字符一律原样保留。
这样即使格式化逻辑有 bug，也不会改变程序的语义——最坏情况是缩进难看，
而不是把学生能跑的代码改成编译不过的代码。

几条刻意不做的事：
  * 不拆行、不合并行、不重排 token（那不是"对齐"，是重写）
  * 不动多行字符串字面量内部的行（改缩进会改变字符串内容！）
  * 不动多行块注释内部的行（很多人用 ASCII 画框）
  * 不动"续行"（上一行括号没闭合的那种），学生往往自己对齐了，
    强行改反而破坏 printf 参数的对齐

和 autofix 一样，用一个跨行的小状态机判断"这一行到底处于什么上下文"，
所以 printf("{") 里的花括号不会被当成块的开头。
"""
from typing import List, NamedTuple, Optional, Tuple

INDENT_WIDTH = 4

# 词法状态
NORMAL = "NORMAL"
IN_STRING = "IN_STRING"
IN_CHAR = "IN_CHAR"
IN_BLOCK_COMMENT = "IN_BLOCK_COMMENT"
IN_LINE_COMMENT = "IN_LINE_COMMENT"


class LineInfo(NamedTuple):
    start_state: str        # 行首所处的词法状态
    code: str               # 只含"代码区"的字符（字符串/注释已剔除）
    has_code: bool          # 代码区里有非空白字符吗
    is_preproc: bool        # 预处理指令（# 开头，或上一行的续行）
    brace_delta: int        # 本行净花括号数
    paren_delta: int        # 本行净圆括号数
    starts_case: bool       # 代码区以 case/default 开头
    first_code_char: str    # 代码区第一个非空白字符


def _scan(text: str) -> List[LineInfo]:
    """逐行扫描，算出每行的结构信息。"""
    out: List[LineInfo] = []
    state = NORMAL
    in_directive = False          # 预处理指令可以跨行（行尾反斜杠）

    for raw in text.split("\n"):
        start_state = state
        code_parts: List[str] = []
        brace = 0
        paren = 0
        i, n = 0, len(raw)
        while i < n:
            ch = raw[i]

            if state == IN_LINE_COMMENT:
                break                                   # 行注释到行尾，后面全是注释
            if state == IN_BLOCK_COMMENT:
                if ch == "*" and i + 1 < n and raw[i + 1] == "/":
                    state = NORMAL
                    i += 2
                    continue
                i += 1
                continue
            if state in (IN_STRING, IN_CHAR):
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                if (state == IN_STRING and ch == '"') or (state == IN_CHAR and ch == "'"):
                    state = NORMAL
                i += 1
                continue

            # ---- NORMAL ----
            if ch == "/" and i + 1 < n and raw[i + 1] == "/":
                state = IN_LINE_COMMENT
                i += 2
                continue
            if ch == "/" and i + 1 < n and raw[i + 1] == "*":
                state = IN_BLOCK_COMMENT
                i += 2
                continue
            if ch == '"':
                state = IN_STRING
                code_parts.append(" ")                  # 占位，保持结构存在
                i += 1
                continue
            if ch == "'":
                state = IN_CHAR
                code_parts.append(" ")
                i += 1
                continue
            if ch == "{":
                brace += 1
            elif ch == "}":
                brace -= 1
            elif ch == "(":
                paren += 1
            elif ch == ")":
                paren -= 1
            code_parts.append(ch)
            i += 1

        if state == IN_LINE_COMMENT:
            state = NORMAL

        code = "".join(code_parts)
        stripped = code.strip()
        is_preproc = in_directive or stripped.startswith("#")
        # 指令续行：行尾反斜杠
        if is_preproc:
            in_directive = stripped.endswith("\\")
        else:
            in_directive = False

        out.append(LineInfo(
            start_state=start_state,
            code=code,
            has_code=bool(stripped),
            is_preproc=is_preproc,
            # 预处理指令里的花括号不计入结构：#define FOO { ... } 很常见，
            # 把它当块开头会让后面整片缩进错位
            brace_delta=0 if is_preproc else brace,
            paren_delta=paren,
            starts_case=(stripped.startswith("case ") or stripped.startswith("default")
                         or stripped.startswith("case\t")),
            first_code_char=stripped[:1],
        ))
    return out


def _strip_ws(s: str) -> str:
    """去掉所有空白，用作"内容没被动过"的校验指纹。"""
    return "".join(s.split())


def format_c_source(text: str, indent_width: int = INDENT_WIDTH) -> Tuple[str, int]:
    """把 C 源码按大括号重新缩进。

    返回 (新文本, 被改动的行数)。**只在确实需要时**才返回不同的文本。
    """
    if not text:
        return text, 0

    trailing_nl = text.endswith("\n")
    body = text[:-1] if trailing_nl else text
    lines = body.split("\n")
    infos = _scan(body)

    unit = " " * max(1, int(indent_width))
    depth = 0
    paren = 0
    changed = 0
    result: List[str] = []

    for raw, info in zip(lines, infos):
        content = raw.strip()

        # ---- 决定这一行要不要重排缩进 ----
        keep = False
        target = 0
        if not info.has_code:
            # 空行：清成空字符串（不留一行空格）
            new_line = ""
        elif info.start_state in (IN_BLOCK_COMMENT, IN_STRING):
            keep = True                       # 多行注释/字符串内部：原样不动
        elif info.is_preproc:
            new_line = content                # 预处理指令顶格
        elif paren > 0:
            keep = True                       # 续行：保留学生自己的对齐
        else:
            d = depth
            if info.first_code_char == "}":
                d = max(0, depth - 1)
            elif info.starts_case:
                d = max(0, depth - 1)          # case 相对 switch 体退一级
            new_line = unit * d + content

        if keep:
            new_line = raw.rstrip()
        elif new_line and not new_line.strip():
            new_line = ""

        if new_line != raw:
            changed += 1
        result.append(new_line)

        # ---- 推进状态 ----
        depth = max(0, depth + info.brace_delta)
        paren = max(0, paren + info.paren_delta)

    new_text = "\n".join(result) + ("\n" if trailing_nl else "")

    # 安全校验：只允许空白变化。万一逻辑有 bug，宁可返回原文。
    if _strip_ws(new_text) != _strip_ws(text):
        return text, 0

    return new_text, changed


def format_c_file(src: str, dst: Optional[str] = None, inplace: bool = False,
                  encoding: Optional[str] = None,
                  indent_width: int = INDENT_WIDTH):
    """格式化文件。默认写副本，不改原文件。

    返回 (是否修改, 输出路径, 改动行数)。
    """
    from .encoding import detect_source_encoding

    enc = encoding or detect_source_encoding(src)
    with open(src, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode(enc)
    except (UnicodeDecodeError, LookupError):
        enc = "utf-8"
        text = raw.decode("utf-8", errors="replace")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    new_text, changed = format_c_source(text, indent_width=indent_width)

    out = src if inplace else (dst or src + ".aligned.c")
    if changed:
        with open(out, "wb") as f:
            f.write(new_text.encode(enc, errors="replace"))
    return bool(changed), out, changed
