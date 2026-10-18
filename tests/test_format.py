#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""代码对齐（缩进格式化）的单元测试。

这个功能的风险和别的功能不一样：它会**改动学生正在写的文件**。
所以测试的重点不是"排得好看"，而是"**没有把代码改坏**"：

1. 最硬的约束：去掉所有空白后，前后内容必须完全一致 —— 任何非空白字符、
   任何 token，一个都不许动。这条守住，格式化最坏只是难看，不会改语义。
2. printf("{") 里的花括号不能被当成块开头（否则后面整片缩进错位）。
3. 多行字符串内部不能动缩进——**改了就是改字符串内容**，这是唯一一个
   "看起来只是缩进、实际改了语义"的地方，必须专门测。
4. 块注释里的 ASCII 画框不能被破坏。
5. 幂等：格式化两次结果必须一样（否则用户按两下会看到内容漂移）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import format as fmt                             # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         "" if cond else "　→ " + str(detail)))


def nows(s):
    return "".join(s.split())


# ---------------------------------------------------------------- 1. 基本缩进
FLAT = (
    "#include <stdio.h>\n"
    "int main(void) {\n"
    "int a, b;\n"
    "scanf(\"%d %d\", &a, &b);\n"
    "if (a > b) {\n"
    "printf(\"a 大\\n\");\n"
    "} else {\n"
    "printf(\"b 大\\n\");\n"
    "}\n"
    "return 0;\n"
    "}\n"
)
out, n = fmt.format_c_source(FLAT)
check("能把顶格的代码按大括号缩进", "    int a, b;" in out, out)
check("if 体内的语句缩进两级", "        printf(\"a 大" in out, out)
check("`} else {` 的收尾大括号退回 if 那一级（这里是 main 体内，4 格）",
      "\n    } else {" in out, out)

# 最硬的安全约束
check("去掉空白后内容完全一致（保证没动任何字符）", nows(out) == nows(FLAT))

# ---------------------------------------------------------------- 2. 花括号在字符串里
STR_CASE = (
    "int main(void) {\n"
    "printf(\"{ 不是块开头 }\\n\");\n"
    "x();\n"
    "return 0;\n"
    "}\n"
)
out_s, _ = fmt.format_c_source(STR_CASE)
check("字符串里的花括号不算块，后面的行缩进不会连锁错位",
      "    x();" in out_s, out_s)
check("字符串内容本身没被改", 'printf("{ 不是块开头 }\\n");' in out_s, out_s)

# ---------------------------------------------------------------- 3. 多行字符串
# 这是唯一一处"改缩进就等于改语义"的地方，必须原样保留
MULTILINE_STR = (
    "int main(void) {\n"
    "    char *s = \"第一行\n"
    "第二行顶格写的内容\n"
    "  第三行故意两个空格\";\n"
    "    return 0;\n"
    "}\n"
)
out_m, _ = fmt.format_c_source(MULTILINE_STR)
check("多行字符串内部的缩进原样保留（改了就是改字符串内容）",
      out_m == MULTILINE_STR, out_m)

# ---------------------------------------------------------------- 4. 块注释
BLOCK_COMMENT = (
    "int main(void) {\n"
    "/*\n"
    " * +--------+\n"
    " * | 成绩表 |\n"
    " * +--------+\n"
    " */\n"
    "return 0;\n"
    "}\n"
)
out_b, _ = fmt.format_c_source(BLOCK_COMMENT)
check("块注释内部的画框不被破坏",
      " * +--------+" in out_b and " * | 成绩表 |" in out_b, out_b)

# ---------------------------------------------------------------- 5. 预处理指令
PREPROC = (
    "#define FOO(x) { x; }\n"
    "#if 1\n"
    "int x = 1;\n"
    "#endif\n"
)
out_p, _ = fmt.format_c_source(PREPROC)
check("预处理指令顶格", out_p.startswith("#define"), out_p)
check("预处理里的花括号不计入缩进结构", "\nint x = 1;" in out_p, out_p)

# ---------------------------------------------------------------- 6. switch / case
SWITCH = (
    "int main(void) {\n"
    "switch (x) {\n"
    "case 1:\n"
    "printf(\"one\\n\");\n"
    "break;\n"
    "default:\n"
    "printf(\"other\\n\");\n"
    "}\n"
    "return 0;\n"
    "}\n"
)
out_sw, _ = fmt.format_c_source(SWITCH)
check("case 相对 switch 体退一级", "\n    case 1:" in out_sw, out_sw)
check("case 下的语句比 case 再进一级", "\n        printf(\"one" in out_sw, out_sw)

# ---------------------------------------------------------------- 7. 续行不动
CONT = (
    "int main(void) {\n"
    "printf(\"%d %d\\n\",\n"
    "       1, 2);\n"
    "return 0;\n"
    "}\n"
)
out_c, _ = fmt.format_c_source(CONT)
check("printf 的续行保留学生自己的对齐（不强行改）",
      "\n       1, 2);" in out_c, out_c)

# ---------------------------------------------------------------- 8. Allman 风格
ALLMAN = (
    "int main(void)\n"
    "{\n"
    "int i;\n"
    "return 0;\n"
    "}\n"
)
out_a, _ = fmt.format_c_source(ALLMAN)
check("大括号独立成行时也能正确缩进",
      out_a.split("\n")[1] == "{" and "    int i;" in out_a, out_a)

# ---------------------------------------------------------------- 9. 幂等
out1, _ = fmt.format_c_source(FLAT)
out2, n2 = fmt.format_c_source(out1)
check("格式化两次结果一样（幂等）", out1 == out2, n2)
check("已经规范的代码不会被再改一遍", n2 == 0, n2)

# ---------------------------------------------------------------- 10. 空行与行尾空格
TRAILING = (
    "int main(void) {   \n"
    "    int a = 1;  \t\n"
    "\n"
    "    return a;\n"
    "}\n"
)
out_t, _ = fmt.format_c_source(TRAILING)
check("行尾空格被清掉", "int main(void) {\n" in out_t, repr(out_t[:30]))
check("空行不残留空格", "\n\n    return a;" in out_t, repr(out_t))
check("空行数量保持不变（不合并、不增删）",
      out_t.count("\n") == TRAILING.count("\n"), (out_t.count("\n"), TRAILING.count("\n")))

# ---------------------------------------------------------------- 11. 边界输入
check("空文本不炸", fmt.format_c_source("") == ("", 0))
check("只有一行不炸", fmt.format_c_source("int main(void){}")[0] == "int main(void){}")
check("没有结尾换行的文本保持没有结尾换行",
      not fmt.format_c_source("int main(void) {\n}")[0].endswith("\n"))

# ---------------------------------------------------------------- 12. 真实文件
sample = os.path.join(ROOT, "tests", "cases", "17_uncovered_duplicate_case.c")
if os.path.exists(sample):
    with open(sample, "rb") as f:
        raw = f.read().decode("utf-8", errors="replace")
    formatted, changed = fmt.format_c_source(raw)
    check("真实样本：去掉空白后内容一致", nows(formatted) == nows(raw))
    ok2, _path, _n = True, None, 0
    try:
        again, n_again = fmt.format_c_source(formatted)
        ok2 = (again == formatted)
    except Exception as e:                                   # noqa: BLE001
        ok2 = False
    check("真实样本：格式化后仍然幂等", ok2)

# ---------------------------------------------------------------- 汇总
print()
failed = [r for r in results if not r[1]]
print("代码对齐单测：%d 项，通过 %d，失败 %d"
      % (len(results), len(results) - len(failed), len(failed)))
for name, _, detail in failed:
    print("  失败：%s　→ %s" % (name, detail))
sys.exit(1 if failed else 0)
