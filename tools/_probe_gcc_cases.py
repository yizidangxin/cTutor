# -*- coding: utf-8 -*-
"""探针：逐个样本用 GCC 编译，看看 GCC 实际报什么。

用途是**校准期望表**——同一个错误在 MSVC 和 GCC 下的错误码/措辞完全不同，
不能拿 MSVC 的期望去要求 GCC。这里把 GCC 的真实输出打出来，人工核对后再写进期望表。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

from engine import compilers, explain                            # noqa: E402
from engine.kb import KnowledgeBase                              # noqa: E402

comp = compilers.detect("gcc")
if comp is None or comp.name != "gcc":
    print("没有可用的 GCC，先设置 CTUTOR_GCC")
    raise SystemExit(1)

kb = KnowledgeBase.load("gcc")
cases_dir = os.path.join(ROOT, "tests", "cases")
build = os.path.join(ROOT, "tests", "build", "_gccprobe")

print("GCC: %s" % comp.gcc)
print("=" * 78)
for name in sorted(os.listdir(cases_dir)):
    if not name.endswith(".c"):
        continue
    src = os.path.join(cases_dir, name)
    res = comp.compile(src, os.path.join(build, name.replace(".c", "")))
    errs = [d for d in res.diagnostics if d.is_error]
    warns = [d for d in res.diagnostics if d.is_warning]
    chosen = explain.choose_diagnostic(res.diagnostics, kb)
    print("%-32s rc=%d 错误%d 警告%d" % (name, res.returncode, len(errs), len(warns)))
    if chosen:
        print("    选中: [%s] code=%r line=%s col=%s" % (
            chosen.level, chosen.code, chosen.line, chosen.column))
        print("    消息: %s" % chosen.message[:80])
        print("    KB命中: %s" % ("是 → " + kb.lookup(chosen).get("title", "")
                                 if kb.lookup(chosen) else "否"))
    else:
        print("    选中: （无诊断）")
    for d in res.diagnostics[:3]:
        print("      · [%s] %r %s" % (d.level, d.code, d.message[:66]))
    print("-" * 78)
