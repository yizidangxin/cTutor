# -*- coding: utf-8 -*-
"""中文特有陷阱检测（结构化）。

为什么需要它（实测依据）：
学生用中文输入法打出全角分号 ；时，MSVC 报的是
    error C2059: 语法错误:“数字上的错误后缀”
这句话对初学者完全不可解释——它甚至没提到标点。GCC 更糟，会显示成
    error: stray '\\357' in program
一行字节码"乱码"。

但这类问题的答案其实就在源码里：只要扫一眼出错行有没有全角字符即可。
这个判断是确定性的（不依赖任何模型），因此可以做到 100% 准确。

输出改为结构化（Hint），使渲染层可以把「原因」与「改法」分别归入对应分层。
"""
from dataclasses import dataclass, field
from typing import List

# 全角字符 -> 对应的半角写法
FULLWIDTH = {
    "；": ";", "，": ",", "：": ":", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "【": "[", "】": "]", "《": "<", "》": ">",
    "！": "!", "？": "?", "。": ".", "、": ",",
    "＋": "+", "－": "-", "＊": "*", "／": "/", "＝": "=",
    "％": "%", "＜": "<", "＞": ">", "＆": "&", "｜": "|",
    "～": "~", "＃": "#", "＠": "@", "＄": "$", "＾": "^",
    # 全角空格必须留在这里：自动修复（autofix）用的就是这张表。
    # 它同时在 INVISIBLE 里（因为「看不见」才是它最难缠的地方），
    # 但提示语由 INVISIBLE 那条负责，见 scan_line 里的排除逻辑。
    "　": " ",
}

# 全角字母与数字（极少见但确实有人误输入）
for _i in range(10):
    FULLWIDTH[chr(0xFF10 + _i)] = str(_i)
for _i in range(26):
    FULLWIDTH[chr(0xFF21 + _i)] = chr(ord("A") + _i)
    FULLWIDTH[chr(0xFF41 + _i)] = chr(ord("a") + _i)

# 不可见字符 -> 说明
#
# 为什么单独列一类：这些东西**肉眼完全看不出来**，但编译器会把它当成名字的一部分，
# 于是报出“xx: 未声明的标识符”，让人以为是拼写错误——按那个方向查永远查不出来。
# 实测（MSVC 14.51）：
#     int　x = 1;        → C2065 “x”: 未声明的标识符        （全角空格 U+3000）
#     int␣a = 1;         → C2065 “int聽a”: 未声明的标识符     （不间断空格 U+00A0，消息里是乱码）
#     char *s = “hi”;    → C2065 “鈥渉i鈥”: 未声明的标识符      （全角引号 + 编码错位，消息乱码）
INVISIBLE = {
    "\u3000": ("全角空格", "删掉它，用普通空格"),
    "\u00a0": ("不间断空格（从 Word / 网页复制带出来的）", "删掉它，用普通空格重新输入"),
    "\u2007": ("数字宽度空格", "删掉它，用普通空格"),
    "\u202f": ("窄不间断空格", "删掉它，用普通空格"),
    "\u200b": ("零宽空格", "删掉它"),
    "\u200c": ("零宽不连字", "删掉它"),
    "\u200d": ("零宽连字", "删掉它"),
    "\ufeff": ("零宽不换行空格（UTF-8 BOM 的字符形态）", "删掉它；文件另存为不带 BOM 的 UTF-8"),
    "\u2028": ("行分隔符", "换成普通换行"),
    "\u2029": ("段分隔符", "换成普通换行"),
}


@dataclass
class Hint:
    kind: str
    title: str                       # 一句话
    cause: str                       # 为什么会这样
    action: str                      # 怎么改
    columns: List[int] = field(default_factory=list)
    details: List[str] = field(default_factory=list)


def find_fullwidth(line_text: str) -> List[tuple]:
    """返回 [(列号(从1开始), 全角字符, 建议的半角写法)]。"""
    hits = []
    for idx, ch in enumerate(line_text, start=1):
        if ch in FULLWIDTH:
            hits.append((idx, ch, FULLWIDTH[ch]))
    return hits


def find_invisible(line_text: str) -> List[tuple]:
    """返回 [(列号, 字符, (名称, 处理建议))]。"""
    return [(i, ch, INVISIBLE[ch])
            for i, ch in enumerate(line_text, start=1) if ch in INVISIBLE]


def looks_mojibake(text: str) -> bool:
    """判断编译器消息里引用的标识符是不是乱码。

    实测：源码是 UTF-8（无 BOM）而里面有全角字符时，MSVC 会按 GBK 去读，
    于是报错消息里打印出来的名字是“鈥渉i鈥”“int聽a”这种乱码。
    遇到这种消息，真正的问题**不是拼写**，而是字符/编码——必须先说清楚，
    否则学生会照“名字拼错了”的方向去查，怎么查都查不出来。

    判据：出现典型的“UTF-8 字节被当作 GBK 解码”产生的字符块，
    例如 鈥 聽 锛 鈥 这类非常用字。
    """
    if not text:
        return False
    # 这些字几乎只会在“UTF-8 被按 GBK 解码”时成串出现
    markers = "鈥聽锛鈥潯鐨勬槸涓轰簡"
    return any(ch in text for ch in markers)


def _code_part(line: str) -> str:
    """取出这一行里"真正是代码"的部分：剔除注释，并剔除字符串/字符字面量内部。

    为什么不能简单 split("//")：
      · `printf("a // b");` 里字符串内部就有 //，直接截断会把后面的全角分号漏掉；
      · `printf("你好，世界");` 里的全角逗号是**合法内容**，报出来就是误报——
        学生照着提示去改，反而把要输出的中文标点改坏了。
    autofix 早就是这么判断的（只在代码区替换），扫提示这边以前没跟上。
    """
    out: List[str] = []
    i, n = 0, len(line)
    quote = ""
    while i < n:
        ch = line[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        if ch == "/" and i + 1 < n and line[i + 1] == "*":
            end = line.find("*/", i + 2)
            if end < 0:
                break
            i = end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def scan_line(line_text: str, max_items: int = 4) -> List[Hint]:
    """对出错行做确定性检查，返回结构化提示。"""
    hints: List[Hint] = []
    if not line_text:
        return hints

    # 注释里的全角字符不算问题；字符串里的更是**故意写的**内容，也不算。
    code_part = _code_part(line_text)
    # 全角空格虽然在 FULLWIDTH 里（自动修复需要它），但提示语交给下面
    # INVISIBLE 那条说——「看不见」才是它真正坑人的地方，措辞不一样。
    fw = [h for h in find_fullwidth(code_part) if h[1] not in INVISIBLE]
    if fw:
        shown = fw[:max_items]
        detail = "、".join("%s → %s" % (c, h) for _, c, h in shown)
        more = "" if len(fw) <= max_items else " 等共 %d 处" % len(fw)
        hints.append(Hint(
            kind="fullwidth",
            title="这一行里有全角字符：%s%s" % (detail, more),
            cause="中文输入法下打出的 ；， ： （ ） 是“全角字符”，占两到三个字节，"
                  "C 语言只认英文半角标点。编译器常常把它描述成看不懂的措辞"
                  "（例如 MSVC 会说“数字上的错误后缀”）。",
            action="把这一行的标点改成英文半角：%s。"
                   "输入法按 Shift 或切到英文状态重新输入即可。" % detail,
            columns=[c for c, _, _ in shown],
            details=["中文注释里的全角字符不影响编译，只有代码部分需要改。",
                     "可以用 `python cli.py fix 文件.c` 让本工具自动替换（注释与字符串内的不动）。"],
        ))

    # 不可见字符：全角空格、从 Word 复制带出的不间断空格、零宽字符……
    # 这类最坑——报错消息会把它算进名字里，看起来像“拼写错误”。
    invis = find_invisible(code_part)
    if invis:
        shown = invis[:max_items]
        names = "、".join(n for _, _, (n, _a) in shown)
        acts = "；".join("%s（第 %d 列）" % (a, c) for c, _ch, (_n, a) in shown)
        hints.append(Hint(
            kind="invisible",
            title="这一行里有**看不见的字符**：%s" % names,
            cause="这些字符在屏幕上和普通空格/什么都没有几乎一样，肉眼查不出来。"
                  "但编译器会把它当成名字或代码的一部分，"
                  "于是报出“xx: 未声明的标识符”这类**指向完全错误方向**的消息——"
                  "按“名字拼错”去查是查不出来的。"
                  "从 Word、网页、PPT 里复制代码最容易带进这些字符。",
            action="删掉它们重新输入：%s。" % acts,
            columns=[c for c, _ch, _ in shown],
            details=[
                "判断窍门：如果报错消息里引用的名字看起来是乱码（比如“鈥渉i鈥”“int聽a”），"
                "那基本就是它，而不是你拼错了。",
                "实在找不到就把这一行整个删掉重打一遍，比盯着看快得多。",
            ],
        ))
    return hints
