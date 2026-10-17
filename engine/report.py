# -*- coding: utf-8 -*-
"""结构化报告：命令行与图形面板共用的唯一接口。

把报告构造从 cli.py 抽出来，好处是「一份 schema，两个消费者」——
界面层不需要解析人类可读文本，只要读这个 JSON 即可。
"""
from typing import Any, Dict, Optional

from . import render

TOOL_VERSION = "0.3.0"
REPORT_SCHEMA = "ctutor/report/1"


def build_report(compiler_name: str, source: str, res, chosen, exp,
                 ai_meta: str = "", depth: int = render.DEFAULT_DEPTH) -> Dict[str, Any]:
    """把一次检查的完整结果打包成结构化报告。"""
    layers = None
    if exp is not None:
        layers = [{"title": t, "lines": b} for t, b in render.layers(exp)]
    return {
        "schema": REPORT_SCHEMA,
        "tool": {"name": "ctutor", "version": TOOL_VERSION},
        "compiler": compiler_name,
        "sarif_status": getattr(res, "sarif_status", ""),
        "timed_out": getattr(res, "timed_out", False),
        "truncated": getattr(res, "truncated", False),
        "notes": list(getattr(res, "notes", []) or []),
        "file": source,
        "returncode": res.returncode,
        "diagnostics": [d.to_dict() for d in res.diagnostics],
        "chosen": chosen.to_dict() if chosen else None,
        "explanation": None if exp is None else {
            "matched": exp.matched,
            "title": exp.title,
            "entry_code": (exp.entry or {}).get("code", ""),
            "layers": layers,
            "rendered": render.render(exp, depth=depth),
            "ai": exp.ai,
            "ai_meta": ai_meta,
        },
    }


def check_once(source: str, depth: int = render.DEFAULT_DEPTH,
               use_ai: bool = False, ai_always: bool = False,
               compiler_prefer: Optional[str] = None,
               workdir: Optional[str] = None) -> Dict[str, Any]:
    """编译 + 解释，返回报告。命令行与面板都走这一条路径，保证行为一致。"""
    from . import ai_explain, config as config_mod, explain as explain_mod
    from . import compilers as compilers_mod
    from .kb import KnowledgeBase

    cfg = config_mod.load()
    comp = compilers_mod.detect(compiler_prefer, cfg)
    if comp is None:
        raise RuntimeError("没有可用的编译器。请放入 bin/w64devkit（GCC）或安装 Visual Studio。")

    wd = workdir or config_mod.path_of("build", cfg)
    res = comp.compile(source, wd)
    kb = KnowledgeBase.load(comp.name)
    chosen = explain_mod.choose_diagnostic(res.diagnostics, kb)
    exp = explain_mod.build(chosen, kb) if chosen else None
    ai_meta = ""

    if exp is not None and use_ai and (ai_always or not exp.matched):
        ai_res = ai_explain.enrich(exp, max_retry=(cfg.get("ai") or {}).get("max_retries", 1))
        if ai_res.ok:
            exp.ai = ai_res.payload
            exp.ai_notes = list(ai_res.notes) + list(ai_res.warnings)
            exp.ai_meta = ai_res.summary()
        else:
            exp.ai_meta = "调用失败：" + ai_res.error
            exp.ai_notes = list(ai_res.warnings)
        ai_meta = exp.ai_meta
    elif exp is not None and use_ai and exp.matched:
        exp.ai_meta = "本地知识库已命中，未调用模型（零成本零延迟）"
        ai_meta = exp.ai_meta

    return build_report(comp.name, source, res, chosen, exp, ai_meta, depth)
