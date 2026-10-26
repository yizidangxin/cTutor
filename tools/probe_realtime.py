# -*- coding: utf-8 -*-
"""验收：① 输出要边跑边出来（不再等程序退出）；② 中文串不能被编译成乱码；
③ GBK 的教材代码也不能被搞坏。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compilers, config as config_mod
from engine import run as run_mod

WD = os.path.join(config_mod.path_of("build"), "realtime_accept")
os.makedirs(WD, exist_ok=True)
comp = compilers.detect(None, config_mod.load())
print("编译器:", comp.name)

SRC = '''#include <stdio.h>
#include <windows.h>

int main(void)
{
    printf("第一行\\n");
    Sleep(700);
    printf("请输入 n: ");
    Sleep(700);
    printf("(提示后继续)\\n");
    return 0;
}
'''


def timed(name, text, encoding):
    path = os.path.join(WD, name)
    with open(path, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
    stamps = []
    t0 = time.time()
    res = run_mod.run_once(path, os.path.join(WD, "w_" + name), timeout=30, compiler=comp,
                          on_output=lambda t: stamps.append((round(time.time() - t0, 3), t)),
                          stdin_queue=None, stdin_text="")
    total = time.time() - t0
    print("\n[%s / %s] 编译成功=%s 返回码=%s 总耗时 %.2fs"
          % (name, encoding, res.compiled, res.exit_code, total))
    for ts, t in stamps:
        print("   %6.3f s  %r" % (ts, t))
    return res, stamps, total


def spread(stamps):
    """第一块和最后一块相隔多久：> 0.5 秒说明是"陆续到的"，不是攒完一起给。"""
    return (stamps[-1][0] - stamps[0][0]) if len(stamps) >= 2 else 0.0


# 第一次跑要算编译时间（2 秒左右），所以"是否实时"看的是分块之间的间隔，
# 不是"第一块早于总耗时多少"。
res, stamps, total = timed("utf8.c", SRC, "utf-8")
print("  → 分批到达（首末间隔 > 0.5 秒）:", spread(stamps) > 0.5, round(spread(stamps), 2))
print("  → 半行提示有没有单独出现:", any("请输入" in t and "(提示" not in t for _, t in stamps))
print("  → 中文是否完好:", "第一行" in res.stdout and "提示后继续" in res.stdout)

res2, stamps2, total2 = timed("gbk.c", SRC, "mbcs")
print("  → GBK 源码中文是否完好:", "第一行" in res2.stdout and "提示后继续" in res2.stdout)
print("  → GBK 分批到达:", spread(stamps2) > 0.5, round(spread(stamps2), 2))