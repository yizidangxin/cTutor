#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""样本集验证器：先有测试样本，再有代码（方案里的验收原则）。

每个样本包含：真实初学者会写的错误代码 + 预期错误码 + 预期级别。
验证器编译每个样本，跑解释器，然后统计：
  ① 是否按预期产生了诊断（解析成功率）
  ② 错误码是否与预期一致（诊断准确率）
  ③ 本地知识库是否命中（覆盖率）
并输出 tests/report.md。

样本内联在本文件中，因此可以精确控制编码（UTF-8 / GBK），
这本身就是中文课堂必须处理的现实问题。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compilers, explain, render              # noqa: E402
from engine.kb import KnowledgeBase                         # noqa: E402

CASES_DIR = os.path.join(HERE, "cases")
BUILD_DIR = os.path.join(HERE, "build")

# ---------------------------------------------------------------- 样本定义
CASES = [
    {
        "id": "01", "name": "01_missing_semicolon.c", "enc": "utf-8",
        "level": "error", "codes": ["C2146"], "note": "漏写分号（最高频）",
        "src": """#include <stdio.h>

int main(void) {
    int x = 10
    printf("%d\\n", x);
    return 0;
}
""",
    },
    {
        "id": "02", "name": "02_fullwidth_semicolon.c", "enc": "gbk",
        "level": "error", "codes": ["C2059", "C2146"], "note": "全角分号（中文输入法，GBK 源码）",
        "src": """#include <stdio.h>

int main(void) {
    int x = 10；
    printf("%d\\n", x);
    return 0;
}
""",
    },
    {
        "id": "03", "name": "03_undeclared_var.c", "enc": "utf-8",
        "level": "error", "codes": ["C2065"], "note": "变量名拼写/未声明",
        "src": """#include <stdio.h>

int main(void) {
    int count = 3;
    printf("%d\\n", cont);
    return 0;
}
""",
    },
    {
        "id": "04", "name": "04_missing_include.c", "enc": "utf-8",
        "level": "warning", "codes": ["C4013", "C3861"], "note": "漏 #include <stdio.h>（MSVC 仅警告）",
        "src": """int main(void) {
    printf("hello\\n");
    return 0;
}
""",
    },
    {
        "id": "05", "name": "05_bad_include.c", "enc": "utf-8",
        "level": "error", "codes": ["C1083"], "note": "头文件名拼错",
        "src": """#include <studio.h>

int main(void) {
    printf("hello\\n");
    return 0;
}
""",
    },
    {
        "id": "06", "name": "06_missing_brace.c", "enc": "utf-8",
        "level": "error", "codes": ["C1075", "C1004", "C1002", "C1020"], "note": "少一个右大括号",
        "src": """#include <stdio.h>

int main(void) {
    int i;
    for (i = 0; i < 3; i++) {
        printf("%d\\n", i);
    return 0;
}
""",
    },
    {
        "id": "07", "name": "07_misspelled_main.c", "enc": "utf-8",
        "level": "error", "codes": ["LNK1561", "LNK2019"], "note": "main 拼成 mian（链接阶段）",
        "src": """#include <stdio.h>

int mian(void) {
    printf("hello\\n");
    return 0;
}
""",
    },
    {
        "id": "08", "name": "08_missing_return.c", "enc": "utf-8",
        "level": "warning", "codes": ["C4716", "C4033", "C4715"], "note": "有返回值的函数漏 return",
        "src": """int add(int a, int b) {
    int s = a + b;
}
""",
    },
    {
        "id": "09", "name": "09_uninitialized.c", "enc": "utf-8",
        "level": "warning", "codes": ["C4700"], "note": "使用了未初始化的变量",
        "src": """#include <stdio.h>

int main(void) {
    int sum;
    sum = sum + 1;
    printf("%d\\n", sum);
    return 0;
}
""",
    },
    {
        "id": "10", "name": "10_format_mismatch.c", "enc": "utf-8",
        "level": "warning", "codes": ["C4477", "C4313"], "note": "printf 格式符与参数类型不匹配",
        "src": """#include <stdio.h>

int main(void) {
    int x = 10;
    printf("%f\\n", x);
    return 0;
}
""",
    },
    {
        "id": "11", "name": "11_scanf_no_amp.c", "enc": "utf-8",
        "level": "warning", "codes": ["C4477", "C4700"], "note": "scanf 漏写 &（能编译，运行会崩）",
        "src": """#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>

int main(void) {
    int x;
    scanf("%d", x);
    printf("%d\\n", x);
    return 0;
}
""",
    },
    {
        "id": "12", "name": "12_vla.c", "enc": "utf-8",
        "level": "error", "codes": ["C2133", "C2057", "C2466", "C2131", "C2087"], "note": "用变量当数组长度（VLA）",
        "src": """#include <stdio.h>

int main(void) {
    int n = 5;
    int a[n];
    a[0] = 1;
    printf("%d\\n", a[0]);
    return 0;
}
""",
    },
    {
        "id": "13", "name": "13_else_without_if.c", "enc": "utf-8",
        "level": "error", "codes": ["C2181"], "note": "if 后多写分号导致 else 悬空",
        "src": """#include <stdio.h>

int main(void) {
    int x = 1;
    if (x > 0);
        printf("pos\\n");
    else
        printf("neg\\n");
    return 0;
}
""",
    },
    {
        "id": "14", "name": "14_ok_hello.c", "enc": "utf-8",
        "level": "none", "codes": [], "note": "负样本：正确程序不应报任何问题",
        "src": """#include <stdio.h>

int main(void) {
    printf("hello\\n");
    return 0;
}
""",
    },
    {
        "id": "15", "name": "15_ok_scanf.c", "enc": "utf-8",
        "level": "none", "codes": [], "note": "负样本：标准 scanf 写法不应报问题",
        "src": """#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>

int main(void) {
    int x;
    scanf("%d", &x);
    printf("%d\\n", x);
    return 0;
}
""",
    },
    {
        "id": "16", "name": "16_gbk_comment_ok.c", "enc": "gbk",
        "level": "none", "codes": [], "note": "负样本：GBK 源码 + 中文注释应正常编译",
        "src": """#include <stdio.h>

int main(void) {
    // 这是中文注释，不应该导致任何报错
    printf("ok\\n");
    return 0;
}
""",
    },
    {
        "id": "17", "name": "17_uncovered_duplicate_case.c", "enc": "utf-8",
        "level": "error", "codes": ["C2196"], "kb_expected": False,
        "note": "switch 重复 case（本地未收录，由第三步 AI 覆盖）",
        "src": """#include <stdio.h>

int main(void) {
    int x = 1;
    switch (x) {
        case 1: printf("one\\n"); break;
        case 1: printf("again\\n"); break;
    }
    return 0;
}
""",
    },
]


def write_cases():
    os.makedirs(CASES_DIR, exist_ok=True)
    for c in CASES:
        path = os.path.join(CASES_DIR, c["name"])
        with open(path, "w", encoding=c["encoding"] if "encoding" in c else c["enc"],
                  newline="\n") as f:
            f.write(c["src"])
    return CASES_DIR


# ------------------------------------------------------------------ GCC 期望表
# 同一个错误，MSVC 和 GCC 的错误码与措辞完全不同，不能用同一套期望去要求两家。
# 本表由 tools/_probe_gcc_cases.py 实测得出（GCC 16.2.0 / w64devkit），逐条人工核对过。
# 写法：样本 id -> (期望级别, [在 code+message 里应当出现的片段])
GCC_EXPECT = {
    "01": ("error", ["expected ',' or ';' before"]),
    "02": ("error", ["stray"]),                       # GBK 下是 stray '\243'
    "03": ("error", ["undeclared"]),
    "04": ("error", ["implicit declaration of function"]),
    "05": ("error", ["No such file or directory"]),
    "06": ("error", ["expected declaration or statement at end of input"]),
    "07": ("error", ["undefined reference to", "main"]),   # 链接错误，只能从文本捕获
    "08": ("warning", ["control reaches end of non-void"]),
    "09": ("warning", ["uninitialized"]),
    "10": ("warning", ["expects argument of type"]),
    # 漏 & 时 GCC 同时给两条警告：格式不匹配（真正的缺陷）与由此推断出的未初始化。
    # 两条都是对的，选哪条取决于排序——和 MSVC 那个样本一样，两条都接受。
    "11": ("warning", ["expects argument of type 'int *'", "uninitialized"]),
    # VLA 在 GCC 下是合法的 C99 特性，只给 -Wvla（默认不开）——所以这里**不该报错**。
    # 这正是"在我电脑上能跑、交上去编译不过"的典型来源，值得保留为对照样本。
    "12": ("none", []),
    "13": ("error", ["'else' without a previous 'if'"]),
    "14": ("none", []),
    "15": ("none", []),
    "16": ("none", []),
    "17": ("error", ["duplicate case value"]),        # 本地未收录，留给 AI 层
}


def expectations(case, compiler_name):
    """按编译器取期望。GCC 若未登记就退回 MSVC 的期望（会显式失败，提醒补登记）。"""
    if compiler_name == "gcc" and case["id"] in GCC_EXPECT:
        return GCC_EXPECT[case["id"]]
    return case["level"], case["codes"]


def matches(diag, expected):
    """错误码或消息里出现任一期望片段即算匹配。

    GCC 的语法错误没有错误码（实测其 SARIF 会把 ruleId 写成 "error"），
    所以 GCC 侧主要靠消息片段匹配；MSVC 侧则是错误码精确命中。
    """
    if not expected:
        return True
    hay = ((diag.code or "") + " " + diag.message).lower()
    return any(e.lower() in hay for e in expected)


def pick(diags, level, kb=None):
    """按预期级别挑出用于比对的那一条诊断（与 CLI 使用同一套选择规则）。"""
    if level == "error":
        return explain.choose_diagnostic([d for d in diags if d.is_error], kb)
    if level == "warning":
        return explain.choose_diagnostic([d for d in diags if d.is_warning], kb)
    return explain.choose_diagnostic(diags, kb)


def main():
    argv = sys.argv[1:]
    verbose = "--verbose" in argv or "-v" in argv
    which = [a for a in argv if not a.startswith("-")]
    show_all = "--all" in argv

    write_cases()
    comp = compilers.detect("auto")
    if comp is None:
        print("没有可用编译器")
        return 3
    kb = KnowledgeBase.load(comp.name)
    print("编译器：%s　知识库条目：%d" % (comp.name, kb.stats()["entries"]))
    print()

    rows, n_ok, n_code, n_kb, n_fail = [], 0, 0, 0, 0
    for c in CASES:
        if which and c["id"] not in which and c["name"] not in which:
            continue
        src = os.path.join(CASES_DIR, c["name"])
        wd = os.path.join(BUILD_DIR, c["id"])
        exp, expected = expectations(c, comp.name)
        res = comp.compile(src, wd)
        diags = res.diagnostics
        errs = [d for d in diags if d.is_error]
        warns = [d for d in diags if d.is_warning]

        got = pick(diags, exp, kb)

        if exp == "none":
            level_ok = (len(errs) == 0 and len(warns) == 0)
            code_ok = level_ok
            got_code = "-"
        else:
            level_ok = got is not None
            got_code = got.code if got else "-"
            code_ok = level_ok and matches(got, expected)

        exp_obj = explain.build(got, kb) if got else None
        kb_ok = bool(exp_obj and exp_obj.matched)

        n_ok += 1 if level_ok else 0
        n_code += 1 if code_ok else 0
        n_kb += 1 if kb_ok else 0
        n_fail += 0 if code_ok else 1

        rows.append({
            "id": c["id"], "note": c["note"], "expect": " / ".join(expected) or "无",
            "level": exp, "got_code": got_code, "level_ok": level_ok,
            "code_ok": code_ok, "kb_ok": kb_ok, "kb_expected": c.get("kb_expected", True),
            "n_diag": len(diags), "message": (got.message if got else ""),
        })

        flag = "PASS" if code_ok else "FAIL"
        print("[%s] %s %-28s 预期 %-24s 实得 %-14s KB:%s"
              % (flag, c["id"], c["note"][:26], (" / ".join(expected) or "无")[:24],
                 got_code or "-", "命中" if kb_ok else "未收录"))
        if not code_ok:
            print("        实际诊断 %d 条，首条消息：%s" % (len(diags),
                  (got.message if got else "(无)")))
            for d in diags[:5]:
                print("          - [%s] %s %s" % (d.level, d.code, d.message[:70]))
        if verbose and exp_obj:
            print(render.render(exp_obj))
        elif show_all and exp_obj:
            print(render.render(exp_obj))

    total = len(rows)
    # KB 覆盖率只统计「有诊断 且 期望本地收录」的样本：
    #   - 负样本（正确程序）本来就没有可解释的东西；
    #   - 标记 kb_expected=False 的样本是刻意留给 AI 层的长尾错误。
    scored = [r for r in rows if r["level"] != "none" and r.get("kb_expected", True)]
    n_scored = len(scored)
    n_kb_scored = sum(1 for r in scored if r["kb_ok"])
    ai_covered = sum(1 for r in rows if r["level"] != "none"
                     and not r.get("kb_expected", True))
    print()
    print("=" * 72)
    print("样本 %d 个（负样本 %d，留给 AI 层的长尾 %d）：级别符合 %d　错误码一致 %d"
          % (total, total - len([r for r in rows if r["level"] != "none"]),
             ai_covered, n_ok, n_code))
    print("诊断准确率 %.0f%%　本地知识库覆盖率 %.0f%%（%d/%d）　不一致 %d"
          % (100.0 * n_code / total if total else 0,
             100.0 * n_kb_scored / n_scored if n_scored else 0,
             n_kb_scored, n_scored, n_fail))
    print("=" * 72)

    # 写报告
    report = os.path.join(HERE, "report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 样本集验证报告\n\n")
        f.write("编译器：`%s`　知识库条目：%d\n\n" % (comp.name, kb.stats()["entries"]))
        f.write("| # | 场景 | 预期错误码 | 实得 | 级别 | 错误码一致 | KB 命中 |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write("| %s | %s | %s | %s | %s | %s | %s |\n" % (
                r["id"], r["note"], r["expect"], r["got_code"], r["level"],
                "是" if r["code_ok"] else "**否**", "是" if r["kb_ok"] else "未收录"))
        f.write("\n**合计**：样本 %d，级别符合 %d，错误码一致 %d，KB 命中 %d\n"
                % (total, n_ok, n_code, n_kb))
    print("报告已写入：%s" % report)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
