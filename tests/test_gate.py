#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""编译验证闸门的集成测试（需要本机有编译器）。

方案里最重要的一条安全设计是：
    **建议必须先应用到代码副本并重新编译，编不过的不展示、不写回。**
这个测试专门验证闸门"会拒绝"，而不只是"会通过"：

  用例 A：只有全角标点错误 → 修复后应编译通过（闸门放行）
  用例 B：全角标点 + 另一个无法自动修复的错误 → 修复后仍编译失败
          → 闸门必须拒绝写回原文件，并如实报告剩余错误
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cli                                                    # noqa: E402
from engine import compilers                                  # noqa: E402

WORK = os.path.join(HERE, "build", "_gate")
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


CASES = {
    # A：只有全角分号 → 应当修复成功
    "a_only_fullwidth.c": (
        "#include <stdio.h>\n\nint main(void) {\n    int x = 10；\n"
        "    printf(\"%d\\n\", x);\n    return 0;\n}\n"),
    # B：全角分号可以自动修，但另外少了一个分号——修完仍然编不过
    "b_unfixable_left.c": (
        "#include <stdio.h>\n\nint main(void) {\n    int x = 10；\n"
        "    int y = 20\n    printf(\"%d\\n\", x + y);\n    return 0;\n}\n"),
}


def main():
    comp = compilers.detect("auto")
    if comp is None:
        print("没有可用编译器，跳过闸门集成测试")
        return 0
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)

    # ---- 用例 A ----
    pa = os.path.join(WORK, "a_only_fullwidth.c")
    with open(pa, "w", encoding="gbk", newline="\n") as f:
        f.write(CASES["a_only_fullwidth.c"])
    ok, data, log, diags = cli._try_deterministic_fix(pa, comp, os.path.join(WORK, "wa"))
    check("A：全角标点可自动修复并编译通过", ok, log)
    check("A：修复后确实变成半角分号", b"int x = 10;" in data.replace(b"\r", b""), data[:80])
    check("A：原文件未被改动",
          open(pa, "rb").read() == CASES["a_only_fullwidth.c"].encode("gbk"))

    # ---- 用例 B（闸门必须拒绝）----
    pb = os.path.join(WORK, "b_unfixable_left.c")
    with open(pb, "w", encoding="gbk", newline="\n") as f:
        f.write(CASES["b_unfixable_left.c"])
    ok_b, data_b, log_b, diags_b = cli._try_deterministic_fix(pb, comp, os.path.join(WORK, "wb"))
    check("B：修复后仍编译失败 → 闸门不放行", not ok_b, ok_b)
    remaining = [d for d in diags_b if d.is_error]
    check("B：如实报告剩余错误", len(remaining) > 0,
          [d.code for d in remaining])
    check("B：原文件未被改动",
          open(pb, "rb").read() == CASES["b_unfixable_left.c"].encode("gbk"))
    print("       剩余错误：" + "、".join("%s %s" % (d.code, d.message[:24]) for d in remaining[:3]))

    n_fail = sum(1 for _, ok in results if not ok)
    print()
    print("=" * 66)
    print("闸门集成测试：%d 项，通过 %d，失败 %d" % (len(results), len(results) - n_fail, n_fail))
    print("=" * 66)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
