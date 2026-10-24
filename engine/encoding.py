# -*- coding: utf-8 -*-
"""编码处理。

中文课堂的真实情况（调研结论 4）：教材光盘与 Dev-C++ 默认 GBK，VS Code 默认 UTF-8，
控制台代码页在 936 与 65001 之间漂移。若不先归一编码：
  - 中文注释会让按「显示列」解析的列号全部错位；
  - 中文标识符会被截断。
因此本模块负责：① 猜测源文件编码；② 把编译器输出（编码不稳定）稳健解码。
"""
import codecs
from typing import List

CANDIDATES = ("utf-8", "mbcs", "oem", "latin-1")


def encoding_label(enc: str) -> str:
    """把 Python 编码名转成给初学者看的中文说法（不要暴露 mbcs 这类内部名称）。"""
    return {
        "utf-8": "UTF-8",
        "utf-8-sig": "UTF-8（带 BOM）",
        "utf-16": "UTF-16",
        "mbcs": "GBK（中文 Windows 的 ANSI 代码页）",
        "oem": "OEM 代码页",
    }.get(enc, enc)


def _normalize(name: str) -> str:
    """把常见编码别名归一（程序输出解码用）。"""
    n = (name or "").strip().lower().replace("-", "").replace("_", "")
    if n in ("utf8", "utf8bom", "utf8sig"):
        return "utf-8"
    if n in ("gbk", "gb2312", "gb18030", "cp936", "936", "ansi"):
        return "gbk"
    if n in ("big5", "cp950"):
        return "big5"
    return n or "utf-8"


def smart_decode(data: bytes) -> str:
    """编译器输出编码不稳定：按候选编码各试一次，取替换字符最少的结果。

    cl.exe 在管道输出时并不总是使用同一编码（实测中同时出现 UTF-8 与 ANSI 片段）。
    """
    best, best_bad = None, None
    for enc in CANDIDATES:
        try:
            s = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        bad = s.count("\ufffd")
        if best_bad is None or bad < best_bad:
            best, best_bad = s, bad
            if bad == 0:
                break
    return best if best is not None else data.decode("utf-8", errors="replace")


def decode_output(raw: bytes, prefer: str = "utf-8") -> str:
    """解码程序输出。

    顺序很讲究：**先严格试 UTF-8**，失败再按源码编码/GBK 兜底。

    为什么不能"按源码编码优先"：GBK 的字节空间很宽，几乎任何字节序列都能解出某个
    结果，所以"GBK 解成功了"根本不是证据。反过来 UTF-8 校验很严，
    一个 GBK 编码的中文串几乎不可能碰巧是合法 UTF-8。所以 UTF-8 严格通过基本就是它。
    （实测：按源码编码优先时，程序输出的中文会变成 תȦ 这样的乱码。）
    """
    if not raw:
        return ""
    prefer = _normalize(prefer)
    order = ["utf-8"]
    for enc in (prefer, "gbk", "cp936", "big5"):
        if enc and enc.lower() not in [o.lower() for o in order]:
            order.append(enc)
    order.append("latin-1")
    for enc in order:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def detect_source_encoding(path: str) -> str:
    """猜测 C 源文件编码。返回 Python 编码名。

    顺序：BOM → 严格 UTF-8 → Windows 系统 ANSI（中文 Windows 即 GBK/936）。
    纯 ASCII 文件会被判为 utf-8，这是正确的（ASCII 是 UTF-8 的子集）。
    """
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    with open(path, "rb") as f:
        raw = f.read()
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "mbcs"


def read_source(path: str) -> "tuple[str, List[str]]":
    """返回 (编码名, 按行拆分的文本)。行尾的 \\r 已去除。"""
    enc = detect_source_encoding(path)
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        enc = "mbcs"
        text = smart_decode(raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return enc, text.split("\n")


def source_lines(path: str, center: int, context: int = 2) -> "tuple[List[str], int]":
    """取第 center 行前后 context 行的源码，返回 (带行号的片段列表, 片段起始行号)。"""
    try:
        _, lines = read_source(path)
    except OSError:
        return [], 0
    start = max(1, center - context)
    end = min(len(lines), center + context)
    out = []
    for n in range(start, end + 1):
        out.append("%4d | %s" % (n, lines[n - 1]))
    return out, start


def col_to_char_index(line_text: str, col: int, compiler: str = "msvc",
                      encoding: str = "utf-8") -> int:
    """把编译器给的**列号**换算成该行里的**字符下标**（0 基）。列号 <=0 时返回 -1。

    这是本项目最容易错、也最难发现的一处（评审把它列为「三个先验证的坑」之首）。
    实测模型（tests/test_colmap.py 用 0~4 个汉字反推，完全吻合）：

        MSVC（GBK 源文件）：ASCII 字符宽 1，双字节字符宽 3。
            汉字数 N=0 → 列=16，N=1 → 19，N=2 → 22，N=4 → 28
            即 列 = 字符列 + 2 × 之前出现的双字节字符数

        GCC：我们给它传了 -fdiagnostics-column-unit=byte，所以列号是 1 基**字节**偏移。

    纯 ASCII 行两种模型一致，因此这个 bug 只有在中文出现时才暴露——
    按字符列直接画插入符，每遇一个汉字就会偏 2 列。
    """
    if col <= 0 or not line_text:
        return -1
    target = col - 1

    if compiler == "gcc":
        try:
            raw = line_text.encode(encoding, errors="replace")
        except LookupError:
            raw = line_text.encode("utf-8", errors="replace")
        prefix = raw[:target]
        try:
            idx = len(prefix.decode(encoding, errors="ignore"))
        except LookupError:
            idx = len(prefix.decode("utf-8", errors="ignore"))
        return min(idx, len(line_text))

    width = 0
    for i, ch in enumerate(line_text):
        if width >= target:
            return i
        width += 1 if ord(ch) < 128 else 3
    return len(line_text)


def char_col(line_text: str, col: int, compiler: str = "msvc",
             encoding: str = "utf-8") -> int:
    """换算成给初学者看的「第几列」（按字符计，与编辑器里看到的一致）。"""
    idx = col_to_char_index(line_text, col, compiler, encoding)
    return idx + 1 if idx >= 0 else col


def caret_line(text: str, column: int, compiler: str = "msvc",
               encoding: str = "utf-8") -> str:
    """在给定列位置生成插入符定位行。

    先做列号语义换算，再按**显示宽度**补空格（CJK 占两列），
    这样插入符才能落在终端里正确的位置。
    """
    idx = col_to_char_index(text, column, compiler, encoding)
    if idx < 0:
        return ""
    pad = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text[:idx])
    return "%s^" % (" " * pad)
