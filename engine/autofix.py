# -*- coding: utf-8 -*-
"""全角标点自动修复（本地确定性修复，可离线、可端到端验证）。

安全性设计：只在**代码区域**替换，绝不改动注释与字符串字面量里的字符——
因为字符串里的中文标点往往是学生故意输出的内容（例如 printf("你好，世界")）。

实现要点：一个跨行的极小词法状态机
    NORMAL / IN_STRING / IN_CHAR / IN_LINE_COMMENT / IN_BLOCK_COMMENT
只有 NORMAL 状态下的全角字符才会被替换。**每个状态都必须有输出分支**——
早期版本漏了 IN_LINE_COMMENT 的 out.append，导致整条行注释被静默吞掉
（单测里 `int a; // 说明；备注` 被吃成 `int a; /`），这类 bug 在纯代码用例上
是看不出来的，因此单测必须覆盖注释场景。
"""
import os
from typing import List, Optional, Tuple

from .hints import FULLWIDTH


def fix_text(text: str) -> Tuple[str, List[Tuple[int, int, str, str]]]:
    """返回 (修复后的文本, [(行号, 列号, 原字符, 半角写法)])。"""
    state = "NORMAL"
    out: List[str] = []
    replaced: List[Tuple[int, int, str, str]] = []
    line_no, col = 1, 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        if ch == "\n":
            out.append(ch)
            line_no += 1
            col = 0
            if state == "IN_LINE_COMMENT":
                state = "NORMAL"
            i += 1
            continue
        col += 1

        if state == "NORMAL":
            if ch == "/" and i + 1 < n and text[i + 1] == "/":
                state = "IN_LINE_COMMENT"
                out.append("//")
                i += 2
                col += 1
                continue
            if ch == "/" and i + 1 < n and text[i + 1] == "*":
                state = "IN_BLOCK_COMMENT"
                out.append("/*")
                i += 2
                col += 1
                continue
            if ch == '"':
                state = "IN_STRING"
                out.append(ch)
                i += 1
                continue
            if ch == "'":
                state = "IN_CHAR"
                out.append(ch)
                i += 1
                continue
            if ch in FULLWIDTH:
                replaced.append((line_no, col, ch, FULLWIDTH[ch]))
                out.append(FULLWIDTH[ch])
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        if state == "IN_LINE_COMMENT":
            out.append(ch)
            i += 1
            continue

        if state == "IN_BLOCK_COMMENT":
            if ch == "*" and i + 1 < n and text[i + 1] == "/":
                out.append("*/")
                state = "NORMAL"
                i += 2
                col += 1
                continue
            out.append(ch)
            i += 1
            continue

        if state == "IN_STRING":
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                col += 1
                continue
            if ch == '"':
                state = "NORMAL"
            out.append(ch)
            i += 1
            continue

        if state == "IN_CHAR":
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                col += 1
                continue
            if ch == "'":
                state = "NORMAL"
            out.append(ch)
            i += 1
            continue

    return "".join(out), replaced


def fix_file(src: str, dst: Optional[str] = None, inplace: bool = False,
             encoding: Optional[str] = None):
    """修复文件代码区中的全角标点。默认写副本，不会改原文件。

    返回 (是否修改, 输出路径, 被替换的位置列表)。
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

    new_text, replaced = fix_text(text)
    out = src if inplace else (dst or src + ".fixed.c")
    if replaced:
        with open(out, "wb") as f:
            f.write(new_text.encode(enc, errors="replace"))
    return bool(replaced), out, replaced
