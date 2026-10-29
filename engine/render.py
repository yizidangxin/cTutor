# -*- coding: utf-8 -*-
"""分层渲染：把一条诊断讲成 5 层，支持按深度渐进披露。

分层（顺序即阅读顺序）：
    1 结论     —— 哪里、什么问题（一句话）
    2 定位     —— 代码片段 + 插入符 + 编译器原文（**位置永远来自编译器**）
    3 原因     —— 为什么会这样（含中文特有陷阱的确定性判断）
    4 改法     —— 具体怎么改（含编译器自带补丁）
    5 知识点   —— 延伸概念与常见坑

内容来源优先级：本地知识库 > AI 补充 > 占位说明。
当某一层来自 AI 时会明确标注——这是 CodeAid 设计原则 D4（透明度与学生可控）的落地。
默认深度 4；将来做「苏格拉底模式」只需把默认深度降到 3，结构不用改。
"""
import html as _html
import re as _re
from typing import Any, Dict, List, Tuple

from .encoding import caret_line, encoding_label
from .explain import Explanation

LAYERS = ("结论", "定位", "原因", "改法", "知识点")
DEFAULT_DEPTH = 4
WIDTH = 66
SEP = "─" * WIDTH

# 知识库、提示语、AI 讲解里到处是 **加粗** 这种 Markdown 写法。
# 终端和 HTML 都不认它：终端把 ** 原样打出来，HTML 里更糟——
# 学生看到「函数没有 return**，所以……**」这种星号，像排版坏了。
# 在渲染层统一处理一次：纯文本去掉标记，HTML 转成 <b>。
_EMPH = _re.compile(r"\*\*(.+?)\*\*", _re.S)


def _plain(s: str) -> str:
    """去掉 Markdown 强调标记（终端 / 纯文本用）。"""
    return _EMPH.sub(r"\1", s)


def _emph_html(s: str) -> str:
    """先转义，再把 **…** 变成 <b>…</b>；内容里夹 HTML 也不会穿透。"""
    return _EMPH.sub(r"<b>\1</b>", _html.escape(s))


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + ln if ln.strip() else "" for ln in text.split("\n"))


def _as_list(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return []


def layers(exp: Explanation) -> List[Tuple[str, List[str]]]:
    """构造 5 层内容。每一层是 (标题, 行列表)。"""
    d, e = exp.diag, exp.entry
    ai: Dict[str, Any] = exp.ai or {}
    out: List[Tuple[str, List[str]]] = []

    # ---------- 1 结论 ----------
    head = exp.title + ("（%s）" % d.code if d.code else "")
    body = [head]
    src = ""
    kb_one = (e or {}).get("one_line")
    if kb_one:
        body.append(kb_one)
        src = "kb"
    elif ai.get("one_line"):
        body.append(str(ai["one_line"]))
        src = "ai"
    else:
        body.append("编译器说：%s" % d.message)
        if exp.ai_meta.startswith("调用失败"):
            body.append("本地知识库未收录，AI 补充也没成功（原因见末尾）。")
        else:
            body.append("这个报错还没有收录进本地知识库（可用 --ai 让模型补充）。")
    if exp.hints:
        body.append("★ " + exp.hints[0].title)
    if src == "ai":
        body.append("（本层讲解由 AI 生成；位置信息仍由编译器给出）")
    out.append(("结论", body))

    # ---------- 2 定位（永远来自编译器） ----------
    loc = ["位置：%s" % exp.location(), "编译器原文：%s" % d.message]
    if exp.snippet:
        loc.append("")
        loc.append("出错位置：")
        loc.extend("  " + s for s in exp.snippet)
        if exp.line_text and d.column:
            caret = caret_line(exp.line_text, d.column, d.compiler, exp.snippet_enc,
                              getattr(d, "col_unit", ""))
            if caret:
                pad = len(exp.snippet[0].split("|")[0]) if exp.snippet else 0
                loc.append("  " + " " * pad + "| " + caret)
    if exp.snippet_enc:
        loc.append("")
        loc.append("源文件编码：%s" % encoding_label(exp.snippet_enc))
    out.append(("定位", loc))

    # ---------- 3 原因 ----------
    why: List[str] = []
    for h in exp.hints:
        why.append("★ %s" % h.title)
        why.append(_indent(h.cause, "      "))
    kb_why = _as_list((e or {}).get("why"))
    ai_why = _as_list(ai.get("why"))
    items, from_ai = (kb_why, False) if kb_why else (ai_why, True)
    if items:
        multi = len(items) > 1
        for i, item in enumerate(items, 1):
            why.append("%d. %s" % (i, item) if multi else item)
        if from_ai:
            why.append("（本层讲解由 AI 生成）")
    if not why:
        why.append("本地知识库尚未收录这个错误码，无法给出原因分析。")
        why.append("可加 --ai 让模型补充，或把编译器原文补进 kb/ 知识库。")
    out.append(("原因", why))

    # ---------- 4 改法 ----------
    fix: List[str] = []
    for h in exp.hints:
        fix.append("★ %s" % h.action)
        for extra in h.details:
            fix.append(_indent("· " + extra, "      "))
    kb_fix = _as_list((e or {}).get("how_to_fix"))
    ai_fix = _as_list(ai.get("how_to_fix"))
    items, from_ai = (kb_fix, False) if kb_fix else (ai_fix, True)
    for i, item in enumerate(items, 1):
        fix.append("%d. %s" % (i, item))
    if items and from_ai:
        fix.append("（本层讲解由 AI 生成）")
    if d.fixits:
        fix.append("")
        fix.append("编译器自带的修复补丁（可直接应用，零猜测）：")
        for f in d.fixits:
            fix.append("    第 %d 行第 %d–%d 字节 → 替换为 %s"
                       % (f.start_line, f.start_col, f.end_col, repr(f.replacement)))
        fix.append("    应用：python cli.py fix <文件>")
    if not fix:
        fix.append("暂无自动修复方案，请按上面的原因手工排查。")
    out.append(("改法", fix))

    # ---------- 5 知识点 ----------
    know: List[str] = []
    pitfalls = _as_list((e or {}).get("pitfalls")) or _as_list(ai.get("pitfalls"))
    concept = (e or {}).get("concept") or ai.get("concept") or ""
    if pitfalls:
        know.append("容易踩的坑：")
        know.extend("    · " + p for p in pitfalls)
    if concept:
        if know:
            know.append("")
        know.append("顺带记住：%s" % concept)
    if from_ai and (pitfalls or concept):
        know.append("（本层讲解由 AI 生成）")
    if not know:
        know.append("（本条暂无延伸知识点）")
    out.append(("知识点", know))
    return out


def render_html(exp: Explanation, depth: int = DEFAULT_DEPTH) -> str:
    """HTML 渲染（供图形面板使用）。

    与终端渲染共用同一份 layers()，因此两个界面的内容保证一致——
    不会出现「命令行说的是 A、面板说的是 B」这种分裂。
    """
    import html as _html

    out = ['<div style="font-family:\'Microsoft YaHei\',\'Segoe UI\',sans-serif;'
           'font-size:10.5pt;color:#1a1a1a;line-height:1.6;">']
    for idx, (title, body) in enumerate(layers(exp)[:depth], 1):
        out.append('<div style="margin:0 0 14px 0;">')
        out.append('<div style="color:#1F3B57;font-weight:600;font-size:11.5pt;'
                   'border-bottom:1px solid #dde3ea;padding-bottom:2px;margin-bottom:6px;">'
                   '【%d %s】</div>' % (idx, title))
        for b in body:
            if not b.strip():
                out.append('<div style="height:6px"></div>')
                continue
            esc = _emph_html(b)
            style = "white-space:pre-wrap;word-break:break-word;margin-left:14px;"
            if b.lstrip().startswith("★"):
                style += "color:#B45309;font-weight:600;"
            elif "由 AI 生成" in b:
                style += "color:#6B7280;font-style:italic;"
            elif b.lstrip().startswith("（") or b.lstrip().startswith("·"):
                style += "color:#4B5563;"
            out.append('<div style="%s">%s</div>' % (style, esc))
        out.append('</div>')

    if depth < len(LAYERS):
        rest = "、".join("%d %s" % (i + 1, LAYERS[i]) for i in range(depth, len(LAYERS)))
        out.append('<div style="color:#6B7280;font-size:9.5pt;">（还有未展开的层：%s）</div>'
                   % rest)
    if exp.ai_meta:
        out.append('<div style="margin-top:8px;color:#6B7280;font-size:9.5pt;">'
                   'AI 补充：%s</div>' % _emph_html(exp.ai_meta))
    for note in exp.ai_notes:
        out.append('<div style="color:#6B7280;font-size:9.5pt;">守卫：%s</div>'
                   % _emph_html(note))
    out.append('</div>')
    return "".join(out)


def render(exp: Explanation, depth: int = DEFAULT_DEPTH, width: int = WIDTH) -> str:
    """按深度渲染。depth 取值 1–5。"""
    depth = max(1, min(len(LAYERS), depth))
    all_layers = layers(exp)
    lines = [SEP]
    for idx, (title, body) in enumerate(all_layers[:depth]):
        if idx > 0:
            lines.append("")
        lines.append("【%d %s】" % (idx + 1, title))
        if idx == 0:
            lines.append("  " + _plain(body[0]))
            for b in body[1:]:
                lines.append(_indent(_plain(b), "  "))
        else:
            for b in body:
                lines.append(_indent(_plain(b), "  ") if b.strip() else "")
    if depth < len(LAYERS):
        remaining = "、".join("%d %s" % (i + 1, LAYERS[i]) for i in range(depth, len(LAYERS)))
        lines.append("")
        lines.append("（还有未展开的层：%s　→ 加 --depth %d 查看）"
                     % (remaining, depth + 1))
    if exp.ai_meta:
        lines.append("")
        lines.append("AI 补充：%s" % exp.ai_meta)
    for note in exp.ai_notes:
        lines.append("守卫：%s" % note)
    lines.append(SEP)
    return "\n".join(lines)
