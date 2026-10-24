# -*- coding: utf-8 -*-
"""解释层：把诊断 + 知识库条目组装成结构化讲解对象。

第一步的输出是平铺的；第二步起由 render.py 按 5 层渲染。本模块只负责
「组装事实」，不负责排版。
"""
from typing import Any, Dict, List, Optional

from .diagnostic import Diagnostic
from .encoding import char_col, read_source, source_lines
from .hints import Hint, looks_mojibake, scan_line
from .kb import KnowledgeBase


class Explanation:
    def __init__(self, diag: Diagnostic, entry: Optional[Dict[str, Any]],
                 snippet: List[str], snippet_start: int, snippet_enc: str,
                 line_text: str, hints: Optional[List[Hint]] = None):
        self.diag = diag
        self.entry = entry
        self.snippet = snippet
        self.snippet_start = snippet_start
        self.snippet_enc = snippet_enc
        self.line_text = line_text
        self.hints = hints or []
        # 第三步：AI 补充的讲解（可能为 None）。位置永远不从这里取。
        self.ai: Optional[Dict[str, Any]] = None
        self.ai_notes: List[str] = []
        self.ai_meta: str = ""

    @property
    def matched(self) -> bool:
        return self.entry is not None

    @property
    def title(self) -> str:
        if self.entry:
            return self.entry.get("title") or "编译器报错"
        return "未收录的错误"

    def location(self) -> str:
        d = self.diag
        if d.is_link_error:
            return "链接阶段（不是某一行的语法错误）"
        if d.line:
            # 显示「字符列」——学生在编辑器里看到的就是这个；
            # 编译器的原始列号语义不同（见 encoding.col_to_char_index），放进 JSON 报告即可。
            if d.column and self.line_text:
                shown = char_col(self.line_text, d.column, d.compiler, self.snippet_enc)
                return "第 %d 行，第 %d 列" % (d.line, shown)
            return "第 %d 行" % d.line
        return "位置未知"


def build(diag: Diagnostic, kb: KnowledgeBase, context: int = 2) -> Explanation:
    """为一个诊断构造解释对象。"""
    snippet: List[str] = []
    start = 0
    line_text = ""
    enc = "utf-8"
    if diag.file and diag.line:
        snippet, start = source_lines(diag.file, diag.line, context)
        try:
            enc, lines = read_source(diag.file)
            if 1 <= diag.line <= len(lines):
                line_text = lines[diag.line - 1]
        except OSError:
            pass
    entry = kb.lookup(diag)
    hints = scan_line(line_text) if line_text else []

    # 编译器消息里引用的名字如果是乱码，真因几乎一定是「字符/编码」而不是「拼写」。
    # 这条必须放在最前面：否则学生会照“名字拼错了”查半天，怎么查都查不出来。
    if looks_mojibake(diag.message):
        hints.insert(0, Hint(
            kind="mojibake",
            title="报错消息里的名字是**乱码** —— 问题不在拼写",
            cause="编译器把源码按错误的编码读取了（比如文件是 UTF-8，它却按 GBK 读），"
                  "于是把几个字节当成了一个奇怪的汉字打出来。"
                  "这种情况**不是你把名字拼错了**，别照那个方向查。",
            action="常见原因有两个：① 源码里混进了全角字符（引号、空格、标点）；"
                   "② 文件编码和编译器假设的不一致。"
                   "先用面板右下角的「编码」下拉换一个试试，"
                   "或者把出错那一行整个删掉、在英文输入法下重打一遍。",
            details=["面板状态栏会显示当前按哪种编码读的文件。",
                     "Dev-C++、教材光盘里的 .c 常是 GBK；VS 里新建的常是 UTF-8。"],
        ))
    return Explanation(diag, entry, snippet, start, enc, line_text, hints)


# 这些警告几乎总是指向**真正的缺陷**，值得优先讲
_HIGH_VALUE_WARN = (
    "C4716", "C4715", "C4033",                 # 漏 return
    "C4700", "C4703",                          # 未初始化
    "C4477", "C4473", "C4313", "C4474",        # 格式串与参数
    "C4706", "C4553",                          # = 写成了 ==
    "C4047", "C4133", "C4090", "C4244", "C4018", "C4267", "C4305",
    "C4172",                                   # 返回局部变量地址
    "C4789", "C4723",                          # 越界 / 除 0
    "-WRETURN-TYPE", "-WRETURN-MISMATCH", "-WUNINITIALIZED", "-WFORMAT",
    "-WPARENTHESES", "-WUNUSED-VALUE", "-WINT-CONVERSION",
    "-WRETURN-LOCAL-ADDR", "-WIMPLICIT-FUNCTION-DECLARATION",
    "-WSIGN-COMPARE", "-WINCOMPATIBLE-POINTER-TYPES", "-WDIV-BY-ZERO",
    "-WARRAY-BOUNDS", "-WIMPLICIT-FALLTHROUGH",
)

# 这些通常只是"没用到"的噪音：既不影响运行，也很少是学生真正想问的问题。
# 必须把它们排到后面——否则一次检查里它们会抢走唯一那个讲解位置，
# 学生看到的是"变量 s 没用到"，而他真正的问题是"函数忘了 return"。
_LOW_VALUE_WARN = (
    "C4101", "C4100", "C4189", "C4456", "C4457", "C4458",
    "-WUNUSED-VARIABLE", "-WUNUSED-BUT-SET-VARIABLE", "-WUNUSED-PARAMETER",
    "-WUNUSED-LABEL", "-WUNUSED-FUNCTION",
)


def choose_diagnostic(diags: List[Diagnostic], kb: Optional[KnowledgeBase] = None):
    """挑选「只给学生讲这一条」的那条诊断。

    规则（依据 Becker 的 Fix the First 原则 + 本项目的可用性目标）：
      1. 有错误 → 取最靠前的一条错误（级联报错里第一条才是根因）；
      2. 只有警告 → **先按「这条警告像不像真缺陷」排**，再考虑知识库能不能解释，最后按位置；
      3. 都没有 → 取最靠前的一条。

    第 2 条是踩过坑之后改的。原来的规则是「挑本地知识库能解释的那条」，
    在知识库还小的时候**碰巧**能用（因为噪音条目还没被收录，自动被跳过）。
    知识库一大，`unused variable` 也被收录了，于是它立刻抢走了讲解位置——
    学生问"函数为什么报错"，得到的是"变量 s 没用到"。

    教训：**"我们能解释"和"这条值得讲"是两件事，不能用前者代替后者。**
    """
    if not diags:
        return None
    ordered = sorted(diags, key=lambda d: d.sort_key())
    errs = [d for d in ordered if d.is_error]
    if errs:
        return errs[0]
    warns = [d for d in ordered if d.is_warning]
    if not warns:
        return ordered[0]

    def rank(d):
        code = (d.code or "").upper()
        noise = 1 if code in _LOW_VALUE_WARN else 0
        # 高价值警告（漏 return、未初始化、格式串错配、= 写成 ==……）优先于
        # 那些只是"没用到"的提示——即使后者恰好被知识库收录了。
        # 这条曾经漏掉：_HIGH_VALUE_WARN 定义了却没用，于是排序退化成
        # "知识库能解释的赢"，正好复现了这条规则的初衷要解决的问题。
        worth = 0 if code in _HIGH_VALUE_WARN else 1
        unexplained = 0 if (kb is not None and kb.lookup(d)) else 1
        return (noise, worth, unexplained, d.sort_key())

    return sorted(warns, key=rank)[0]
