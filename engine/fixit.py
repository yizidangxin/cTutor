# -*- coding: utf-8 -*-
"""编译器自带修复补丁（GCC -fdiagnostics-parseable-fixits）的解析与应用。

为什么重要：缺分号、拼错标识符这类高频错误的修复，**编译器自己就知道**。
直接用它的补丁可以做到零幻觉、零 token、毫秒级——这正是「确定性层」的价值。

GCC 的位置语义（官方文档原文）：the location is expressed as a half-open range,
expressed as a **count of bytes**, starting at byte 1 for the initial column.
因此本模块按**字节**操作，而不是按字符，否则中文注释会让偏移全错
（我们同时给 GCC 传了 -fdiagnostics-column-unit=byte 保持一致）。
"""
import difflib
from typing import List, Tuple

from .diagnostic import FixIt


class ApplyResult:
    def __init__(self, changed: bool, data: bytes, applied: int, skipped: List[str]):
        self.changed = changed
        self.data = data
        self.applied = applied
        self.skipped = skipped


def apply_fixits_to_bytes(data: bytes, fixits: List[FixIt], encoding: str = "utf-8") -> ApplyResult:
    """把 fix-it 应用到字节序列上，返回新字节。按行从后往前应用以保持偏移有效。"""
    lines = data.split(b"\n")
    edits = {}          # line_index -> [(start, end, replacement_bytes)]
    skipped: List[str] = []

    for f in fixits:
        if f.start_line != f.end_line:
            skipped.append("跨行修复暂不支持：第 %d–%d 行" % (f.start_line, f.end_line))
            continue
        idx = f.start_line - 1
        if idx < 0 or idx >= len(lines):
            skipped.append("行号越界：第 %d 行" % f.start_line)
            continue
        try:
            repl = f.replacement.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            repl = f.replacement.encode("utf-8")
        start = max(0, f.start_col - 1)
        end = max(start, f.end_col - 1)
        edits.setdefault(idx, []).append((start, end, repl))

    applied = 0
    for idx, group in edits.items():
        line = lines[idx]
        for start, end, repl in sorted(group, key=lambda t: t[0], reverse=True):
            if start > len(line):
                skipped.append("列号越界：第 %d 行第 %d 列" % (idx + 1, start + 1))
                continue
            line = line[:start] + repl + line[end:]
            applied += 1
        lines[idx] = line

    return ApplyResult(applied > 0, b"\n".join(lines), applied, skipped)


def unified_diff_bytes(a: bytes, b: bytes, name: str, enc: str = "utf-8") -> str:
    def dec(x):
        try:
            return x.decode(enc)
        except (UnicodeDecodeError, LookupError):
            return x.decode("utf-8", errors="replace")
    return "".join(difflib.unified_diff(
        dec(a).splitlines(True), dec(b).splitlines(True),
        fromfile=name + "（原始）", tofile=name + "（修复后）"))
