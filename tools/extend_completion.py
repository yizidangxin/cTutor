# -*- coding: utf-8 -*-
"""扩充补全词条（幂等）。

需求："添加更多的库比如 #include <time.h>，再合理拓展其他的东西"

做法分两半：
  1. **include 片段本身升级**：它原本就是一个带下拉的头文件选择器
     （`#include <${1|stdio.h,stdlib.h,string.h,math.h|}>`），
     把 time.h / ctype.h 加进下拉列表，学生一打 include 就能选到。
  2. **补上这些库里最常用的函数**，每条都带中文说明——补全本身也是教学机会。

尺度（沿用数据文件开头的原则）：**保持精简**。初学者面对上百条建议只会更困惑，
所以只收"课本里一定会出现"的那些，不收文件 IO、信号、线程这类。

跑法：
    python tools/extend_completion.py          # 写入
    python tools/extend_completion.py --check  # 只看会加什么

幂等：标签已存在就跳过（不会重复插入，也不会覆盖你手动改过的条目）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = os.path.join(ROOT, "data", "completion.json")

# include 片段的新下拉列表（顺序按教学出场先后：先 stdio，再常用，最后 time/ctype）
INCLUDE_CHOICES = "stdio.h,stdlib.h,string.h,math.h,time.h,ctype.h"

NEW_ITEMS = [
    # ---------------- time.h ----------------
    {"label": "time", "kind": "function", "detail": "time.h · 当前时间（秒）",
     "doc": "time(NULL) 返回从 1970 年到现在过了多少秒，常用来给 srand 播种："
            "srand(time(NULL))。需要 #include <time.h>。"},
    {"label": "ctime", "kind": "function", "detail": "time.h · 时间转文字",
     "doc": "ctime(&t) 把时间秒数变成 \"Mon Jan  1 12:00:00 2020\" 这样的字符串，"
            "直接 printf(\"%s\", ctime(&t)) 就能打印出来。需要 #include <time.h>。"},
    {"label": "clock", "kind": "function", "detail": "time.h · 程序跑了多久",
     "doc": "返回程序占用 CPU 的时间（单位 CLOCKS_PER_SEC）。"
            "想测一段代码多快，前后各取一次相减。需要 #include <time.h>。"},
    {"label": "difftime", "kind": "function", "detail": "time.h · 两个时间差几秒",
     "doc": "difftime(结束, 开始) 返回两者相差的秒数（double）。"
            "比直接相减更清楚，也不会因为类型问题出错。"},
    {"label": "localtime", "kind": "function", "detail": "time.h · 拆成本地时间",
     "doc": "localtime(&t) 返回 struct tm 指针，里面有 tm_year（要 +1900）、"
            "tm_mon（要 +1）、tm_mday、tm_hour、tm_min、tm_sec。需要 #include <time.h>。"},
    {"label": "strftime", "kind": "function", "detail": "time.h · 格式化时间",
     "doc": "strftime(缓冲区, 长度, \"%Y-%m-%d %H:%M\", &tm) 把时间写成你要的格式，"
            "比如 2026-10-19 21:30。需要 #include <time.h>。"},
    {"label": "time_t", "kind": "type", "detail": "time.h · 时间类型",
     "doc": "存时间秒数的整数类型。写 time_t t = time(NULL); 就行，"
            "不要自己猜它是 int 还是 long。需要 #include <time.h>。"},
    {"label": "tm", "kind": "struct", "detail": "time.h · 年月日时分秒结构",
     "doc": "struct tm 是 localtime 拆出来的时间。注意 tm_year 存的是『从 1900 年起』，"
            "tm_mon 是 0–11，所以打印时要 +1900 和 +1——这是初学者最常错的地方。"},

    # ---------------- ctype.h ----------------
    {"label": "isalpha", "kind": "function", "detail": "ctype.h · 是不是字母",
     "doc": "是字母返回非 0，否则返回 0。注意它返回的是 int，不是 bool。"
            "需要 #include <ctype.h>。"},
    {"label": "isdigit", "kind": "function", "detail": "ctype.h · 是不是数字字符",
     "doc": "判断的是字符 '0'–'9'，不是数值。要判断数字大小用比较运算。"
            "需要 #include <ctype.h>。"},
    {"label": "isspace", "kind": "function", "detail": "ctype.h · 是不是空白",
     "doc": "空格、制表符、换行都算空白。统计单词数时常用它跳过空白。"
            "需要 #include <ctype.h>。"},
    {"label": "toupper", "kind": "function", "detail": "ctype.h · 转大写",
     "doc": "toupper('a') 得到 'A'；本来就是大写或不是字母则原样返回。"
            "需要 #include <ctype.h>。"},
    {"label": "tolower", "kind": "function", "detail": "ctype.h · 转小写",
     "doc": "tolower('A') 得到 'a'。做大小写不敏感的字符串比较时会用到。"
            "需要 #include <ctype.h>。"},

    # ---------------- string.h 补充 ----------------
    {"label": "memset", "kind": "function", "detail": "string.h · 批量填值",
     "doc": "memset(数组, 0, sizeof(数组)) 把整块内存清零——初始化数组最常用。"
            "注意第二个参数是**字节值**，不是要填的整数。需要 #include <string.h>。"},
    {"label": "memcpy", "kind": "function", "detail": "string.h · 复制内存块",
     "doc": "memcpy(目标, 源, 字节数) 复制任意数据；字符串用 strcpy 更直观。"
            "需要 #include <string.h>。"},
    {"label": "strchr", "kind": "function", "detail": "string.h · 找字符",
     "doc": "strchr(字符串, 'x') 返回第一次出现的位置（指针），没找到返回 NULL。"
            "需要 #include <string.h>。"},

    # ---------------- math.h 补充 ----------------
    {"label": "floor", "kind": "function", "detail": "math.h · 向下取整",
     "doc": "floor(3.7) 得 3.0，floor(-3.2) 得 -4.0（往小的方向）。"
            "返回的仍是小数类型，要整数请强制转换。#include <math.h>。"},
    {"label": "ceil", "kind": "function", "detail": "math.h · 向上取整",
     "doc": "ceil(3.2) 得 4.0。和 floor 相反。#include <math.h>。"},
    {"label": "fmod", "kind": "function", "detail": "math.h · 小数取余",
     "doc": "fmod(5.5, 2.0) 得 1.5。注意 % 只能用于整数，小数取余必须用 fmod。"
            "#include <math.h>。"},
    {"label": "M_PI", "kind": "value", "detail": "math.h · 圆周率",
     "doc": "3.14159…。有些编译器要加 -D_USE_MATH_DEFINES 才能用，"
            "不确定就直接写 3.14159265。#include <math.h>。"},

    # ---------------- stdlib.h 补充 ----------------
    {"label": "qsort", "kind": "function", "detail": "stdlib.h · 快速排序",
     "doc": "qsort(数组, 个数, 每个多大, 比较函数)。比较函数要自己写，"
            "返回负数表示前面更小。#include <stdlib.h>。"},
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告，不写入")
    args = ap.parse_args(argv)

    with open(DATA, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    items = data.get("items") or []
    by_label = {it.get("label"): it for it in items}

    changed = []

    # ---- 1. 升级 include 片段的下拉列表 ----
    inc = by_label.get("include")
    if inc is not None:
        old = inc.get("insert") or ""
        new = "#include <${1|%s|}>" % INCLUDE_CHOICES
        if "time.h" not in old:
            inc["insert"] = new
            inc["doc"] = (
                "把函数说明书引进来。printf/scanf 需要 stdio.h，strlen/strcpy 需要 string.h，"
                "malloc/free 需要 stdlib.h，sqrt/pow 需要 math.h，"
                "time/rand 播种需要 time.h，isalpha/isdigit 需要 ctype.h。")
            changed.append("升级 include 片段（下拉里加了 time.h / ctype.h）")
        else:
            print("include 片段已包含 time.h，跳过")

    # ---- 2. 追加新词条 ----
    added = 0
    for it in NEW_ITEMS:
        if it["label"] in by_label:
            continue
        items.append(it)
        by_label[it["label"]] = it
        added += 1

    # 按 kind 重算统计
    kinds = dict(data.get("kinds") or {})
    for it in items:
        k = it.get("kind", "?")
        kinds[k] = kinds.get(k, 0) + 0      # 保持原有种类表，不新增未知种类
    # 只统计已有种类，避免写出非法 kind 映射
    allowed = set(kinds.keys()) or {"keyword", "function", "snippet", "struct", "type", "value"}
    bad = [it["label"] for it in items if it.get("kind") not in allowed]
    if bad:
        print("⚠ 有词条的 kind 不在已知种类里：%s" % bad[:5])

    data["items"] = items
    data["version"] = int(data.get("version") or 1) + (1 if (added or changed) else 0)

    print("新增 %d 条（原有 %d → 现在 %d）" % (added, len(items) - added, len(items)))
    for c in changed:
        print("· " + c)
    print("kind 分布：", {k: sum(1 for i in items if i.get("kind") == k) for k in sorted(allowed)})

    if args.check:
        print("（--check：未写入）")
        return 0
    if not (added or changed):
        print("没有需要改的")
        return 0

    with open(DATA, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("已写入 %s" % DATA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
