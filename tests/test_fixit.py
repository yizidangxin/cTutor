#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""确定性修复的单元测试。

为什么要单独测：
- GCC 的 fix-it 位置是**字节**偏移（官方文档明确），若按字符处理，中文会让偏移全错；
  这类错误在纯英文用例上测不出来，必须用中文构造用例。
- 全角标点修复必须**只动代码区**，不能碰注释和字符串——否则会把学生故意输出的
  中文标点（printf("你好，世界")）改坏。
本机没有 GCC，因此用官方文档给出的 fix-it 文本格式构造用例，先验证解析与应用的
正确性；端到端（真编译器产出 fix-it）需放入 w64devkit 后再测。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import autofix, fixit, textparse                 # noqa: E402
from engine.diagnostic import FixIt                          # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         "" if cond else "　→ " + str(detail)))


# ---------------------------------------------------------------- 1. fix-it 解析
gcc_text = (
    "a.c:5:3: error: 'gtk_widget_showall' undeclared (first use in this function)\n"
    'fix-it:"a.c":{5:3-5:21}:"gtk_widget_show_all"\n'
    "a.c:9:1: warning: unused variable 'x' [-Wunused-variable]\n"
    'fix-it:"a.c":{9:1-9:2}:""\n'
)
diags, fixits = textparse.parse_gcc_text(gcc_text)
check("GCC 文本解析出 2 条诊断", len(diags) == 2, len(diags))
check("GCC fix-it 解析出 2 条", len(fixits) == 2, len(fixits))
check("fix-it 区间与替换文本正确",
      fixits and fixits[0].start_line == 5 and fixits[0].start_col == 3
      and fixits[0].end_col == 21 and fixits[0].replacement == "gtk_widget_show_all",
      fixits[0] if fixits else None)
check("空替换字符串=删除",
      len(fixits) > 1 and fixits[1].replacement == "",
      fixits[1].replacement if len(fixits) > 1 else None)

# ---------------------------------------------------------------- 2. 字节语义（关键）
# 纯英文行：在第 11 字节插入分号
data = b"int x = 10\nint y = 20;\n"
r = fixit.apply_fixits_to_bytes(data, [FixIt("a.c", 1, 11, 1, 11, ";")], "utf-8")
check("ASCII：按字节插入分号", r.changed and r.data.split(b"\n")[0] == b"int x = 10;",
      r.data.split(b"\n")[0])

# 中文行：UTF-8 下「中文」占 6 字节，分号在第 7 字节。
# 若实现按「字符」计数，第 7 列会越界——这正是中文场景必须用字节的原因。
data = "中文;\n".encode("utf-8")
r = fixit.apply_fixits_to_bytes(data, [FixIt("a.c", 1, 7, 1, 8, "")], "utf-8")
check("中文：按字节删除第 7 字节（字符偏移会越界）",
      r.changed and r.data == "中文\n".encode("utf-8"), r.data)

# 中文行插入：在「中文」之后（第 7 字节）插入 !
data = "中文\n".encode("utf-8")
r = fixit.apply_fixits_to_bytes(data, [FixIt("a.c", 1, 7, 1, 7, "!")], "utf-8")
check("中文：按字节插入", r.data == "中文!\n".encode("utf-8"), r.data)

# 跨行修复应被安全跳过
r = fixit.apply_fixits_to_bytes(b"abc\n", [FixIt("a.c", 1, 1, 2, 2, "x")], "utf-8")
check("跨行修复被跳过且不报错", (not r.changed) and len(r.skipped) == 1, r.skipped)

# ---------------------------------------------------------------- 3. 全角修复：只动代码区
cases = [
    ("代码里的全角分号被替换",
     "int x = 10；\n", "int x = 10;\n", 1),
    ("字符串里的全角标点不动",
     'printf("你好，世界\\n");\n', 'printf("你好，世界\\n");\n', 0),
    ("行注释里的全角标点不动",
     "int a; // 说明；备注\n", "int a; // 说明；备注\n", 0),
    ("块注释跨行也不动",
     "/* 第一行；\n   第二行，\n*/\nint a；\n", "/* 第一行；\n   第二行，\n*/\nint a;\n", 1),
    ("字符常量里的全角标点不动",
     "char c = '；';\n", "char c = '；';\n", 0),
    ("转义引号不会提前结束字符串",
     'printf("a\\"；b");\n', 'printf("a\\"；b");\n', 0),
    ("全角括号与冒号一起替换",
     "if（x > 0）:\n", "if(x > 0):\n", 2),
    ("全角空格被替换",
     "int\u3000a = 1;\n", "int a = 1;\n", 1),
]
for name, src, want, want_n in cases:
    got, replaced = autofix.fix_text(src)
    check("全角修复：" + name,
          got == want and len(replaced) == want_n,
          "得到 %r，替换 %d 处（期望 %r / %d 处）" % (got, len(replaced), want, want_n))

# ---------------------------------------------------------------- 4. 汇总
n_fail = sum(1 for _, ok, _ in results if not ok)
print()
print("=" * 66)
print("确定性修复单测：%d 项，通过 %d，失败 %d"
      % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)
