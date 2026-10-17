#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库覆盖率测试：拿真实初学者错误样本，跑「编译 → 选诊断 → 查知识库」全流程。

和 run_cases.py 的区别：
  · run_cases 校验的是「选中的错误码对不对」（诊断准确率）
  · 本文件校验的是「选中的那条能不能被中文解释」（知识库覆盖率）

后者才是学生真正感受到的东西——码选对了但"未收录"，等于白搭。
样本来自 tools/probe_snippets.py，每个都在真实编译器上验证过。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compilers, config as config_mod, explain       # noqa: E402
from engine.kb import KnowledgeBase                               # noqa: E402
from probe_snippets import SNIPPETS                               # noqa: E402

# 这些样本**故意**不要求知识库覆盖：
#   · 编译器根本没报错的（属于运行时逻辑问题，不该由报错解释器兜）
#   · 留给 AI 层的长尾样本
EXPECTED_MISS = {
    "指针和整数比较",      # MSVC 默认不开 /W4，无诊断
    "switch 忘写 break",   # MSVC 不报；GCC 有 -Wimplicit-fallthrough
    "同名不同类型",        # 只是遮蔽警告，属边界
    "变量当数组长度（VLA）",  # GCC 下合法，MSVC 报错但语义特殊
    "数组下标越界常量",     # GCC 默认算不出来
}

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


def main() -> int:
    cfg = config_mod.load()
    comp = compilers.detect(None, cfg)
    if comp is None:
        print("跳过：本机没有可用编译器")
        return 0
    kb = KnowledgeBase.load(comp.name)
    print("编译器：%s　知识库：%s（%d 条）" % (comp.name, comp.name, len(kb.entries)))
    print()

    work = os.path.join(config_mod.path_of("build"), "kb_coverage")
    os.makedirs(work, exist_ok=True)

    hit, miss, nodiag, skipped = [], [], [], []
    for idx, (name, cat, src) in enumerate(SNIPPETS):
        d = os.path.join(work, "%02d" % idx)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "t.c")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        res = comp.compile(path, d)
        chosen = explain.choose_diagnostic(res.diagnostics, kb)
        if chosen is None:
            nodiag.append(name)
            continue
        exp = explain.build(chosen, kb)
        if exp.matched:
            hit.append((name, exp.title))
        else:
            miss.append((name, chosen.code or "(无码)", chosen.message[:60]))
        if name in EXPECTED_MISS:
            skipped.append(name)

    total = len(SNIPPETS)
    judged = total - len(nodiag)
    print()
    print("=" * 74)
    print("知识库覆盖率")
    print("=" * 74)
    print("  样本总数      : %d" % total)
    print("  编译器无诊断  : %d（%s）" % (len(nodiag), "、".join(nodiag) or "无"))
    print("  参与判定      : %d" % judged)
    print("  命中知识库    : %d" % len(hit))
    print("  未命中        : %d" % len(miss))
    rate = (len(hit) / judged * 100) if judged else 0
    print("  覆盖率        : %.0f%%" % rate)
    print()

    real_miss = [(n, c, m) for (n, c, m) in miss if n not in EXPECTED_MISS]
    if real_miss:
        print("  真实未覆盖（应当补）：")
        for n, c, m in real_miss:
            print("    · %-22s %-12s %s" % (n, c, m))
        print()
    expected_missed = [(n, c, m) for (n, c, m) in miss if n in EXPECTED_MISS]
    if expected_missed:
        print("  预期内未覆盖（已在样本表里登记原因）：")
        for n, c, m in expected_missed:
            print("    · %-22s %-12s %s" % (n, c, m))
        print()

    print("  命中明细：")
    for n, t in hit:
        print("    ✓ %-22s %s" % (n, t[:44]))
    print()

    check("知识库覆盖率 ≥ 90%%（真实未覆盖为 0）", rate >= 90 and not real_miss,
          "覆盖率 %.0f%%，真实未覆盖 %d 个" % (rate, len(real_miss)))
    check("每个新条目都能被独立检索到（不是靠别条兜底）",
          all(t for _n, t in hit))

    n_fail = sum(1 for _, ok in results if not ok)
    print("=" * 74)
    print("知识库覆盖测试：%d 项，通过 %d，失败 %d"
          % (len(results), len(results) - n_fail, n_fail))
    print("=" * 74)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
