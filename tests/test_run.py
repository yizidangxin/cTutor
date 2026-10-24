#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""运行功能的测试。

重点是那些**初学者程序每天都会出现**的状态，而不是"能跑通"：
死循环、等输入、输出刷屏、数组越界崩溃、源码带 BOM。
这些情况如果处理错了，表现往往是"结果莫名其妙"而不是报错，必须用测试钉住。
"""
import os
import queue
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compilers                              # noqa: E402
from engine import config as config_mod                   # noqa: E402
from engine import run as run_mod                         # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


WORK = os.path.join(config_mod.path_of("build"), "test_run")


def write_case(name, src, enc="utf-8"):
    os.makedirs(WORK, exist_ok=True)
    p = os.path.join(WORK, name)
    with open(p, "w", encoding=enc, newline="\n") as f:
        f.write(src)
    return p


# ---------------------------------------------------------------- 纯函数
print("— 编码处理 —")
check("utf-8-sig 归一化成 utf-8（否则 BOM 会混进 stdin）",
      run_mod.normalize_encoding("utf-8-sig") == "utf-8"
      and run_mod.normalize_encoding("UTF_8_SIG") == "utf-8")
check("普通编码名保持不变", run_mod.normalize_encoding("gbk") == "gbk")

check("解码 UTF-8 中文", run_mod.decode_output("你好，世界".encode("utf-8")) == "你好，世界")
check("解码 GBK 中文", run_mod.decode_output("你好，世界".encode("gbk")) == "你好，世界")
check("纯 ASCII 两种编码都对", run_mod.decode_output(b"hello 123") == "hello 123")
check("空输出返回空串", run_mod.decode_output(b"") == "")
check("非法字节不会抛异常（有兜底）", isinstance(run_mod.decode_output(b"\xff\xfe\x00abc"), str))
# 这条是关键：GBK 几乎能解任何字节，所以不能"谁先成功用谁"——UTF-8 优先才不会乱码
check("UTF-8 的内容不会被误判成 GBK（乱码回归）",
      run_mod.decode_output("转圈".encode("utf-8"), prefer="gbk") == "转圈",
      run_mod.decode_output("转圈".encode("utf-8"), prefer="gbk"))

print()
print("— 返回码翻译成人话 —")
for code, keyword in ((0xC0000005, "数组越界"), (0xC0000094, "除以 0"),
                      (0xC00000FD, "栈溢出"), (0xC0000409, "越界")):
    r = run_mod.RunResult(compiled=True, exit_code=code)
    reason = r.exit_reason()
    check("0x%08X 翻译出「%s」" % (code, keyword), keyword in reason, reason)
check("返回码 0 时没有异常说明", run_mod.RunResult(exit_code=0).exit_reason() == "")
check("普通非 0 返回码也有说明", "非 0" in run_mod.RunResult(exit_code=3).exit_reason())
check("超时不重复报返回码",
      run_mod.RunResult(exit_code=-9, timed_out=True).exit_reason() == "")

print()
print("— 结果排版 —")
r = run_mod.RunResult(compiled=True, ok=True, stdout="7\n", duration=0.1)
txt = run_mod.format_result(r)
check("正常结果有表头和用时", "运行结果" in txt and "正常结束" in txt and "7" in txt, txt)
r2 = run_mod.RunResult(compiled=True, ok=True, stdout="", duration=0.1)
check("没有输出时明确写出来（而不是显示成空白）",
      "没有任何输出" in run_mod.format_result(r2))
r3 = run_mod.RunResult(compiled=False)
check("编译未通过时不谈运行结果",
      "编译未通过，未运行" in run_mod.format_result(r3))

# ---------------------------------------------------------------- 真编译真运行
print()
print("— 独立工作目录（避免「上一个窗口还开着」导致编译失败）—")
_base = os.path.join(WORK, "uniq")
_w1 = run_mod.unique_workdir(_base)
_w2 = run_mod.unique_workdir(_base)
check("每次都拿到不同的工作目录", _w1 != _w2, (_w1, _w2))
check("目录确实被创建", os.path.isdir(_w1) and os.path.isdir(_w2))
# 造一个"被占用"的场景：目录里放个文件并占住它，确认新的一次仍能编译运行
_open_holder = open(os.path.join(_w1, "a.exe"), "wb")
_src_uniq = write_case("uniq.c", '#include <stdio.h>\nint main(void){printf("ok\\n");return 0;}\n')
_comp_uniq = compilers.detect(None, config_mod.load())
if _comp_uniq is not None:
    _r = run_mod.run_once(_src_uniq, _w2, source_encoding="utf-8", compiler=_comp_uniq)
    check("上一个目录被占用时，新的一次照样能编译运行",
          _r.compiled and _r.ok, (_r.compiled, _r.notes))
_open_holder.close()

print()
print("— 真编译真运行 —")
comp = compilers.detect(None, config_mod.load())
if comp is None:
    print("[SKIP] 本机没有可用编译器")
else:
    print("编译器：%s" % comp.name)

    # 1) 正常程序 + 输入
    src = write_case("ok.c", """#include <stdio.h>
int main(void) {
    int a, b;
    if (scanf("%d %d", &a, &b) != 2) { printf("读取失败\\n"); return 1; }
    printf("和是 %d\\n", a + b);
    return 0;
}
""")
    res = run_mod.run_once(src, os.path.join(WORK, "t1"), stdin_text="3 4",
                           source_encoding="utf-8", compiler=comp)
    check("正常程序：编译通过且返回码 0", res.compiled and res.ok,
          "%s / %s" % (res.compiled, res.exit_code))
    check("正常程序：stdin 真的喂进去了（应是 7）", "和是 7" in res.stdout, res.stdout)

    # 2) 源码带 BOM 时，输入不能被 BOM 污染（真实踩过的 bug）
    src_bom = write_case("ok_bom.c", """#include <stdio.h>
int main(void) {
    int a, b;
    if (scanf("%d %d", &a, &b) != 2) { printf("读取失败\\n"); return 1; }
    printf("和是 %d\\n", a + b);
    return 0;
}
""", enc="utf-8-sig")
    res_bom = run_mod.run_once(src_bom, os.path.join(WORK, "t2"), stdin_text="5 6",
                               source_encoding="utf-8-sig", compiler=comp)
    check("带 BOM 的源码：输入仍然正确（BOM 不能进 stdin）",
          "和是 11" in res_bom.stdout, res_bom.stdout)

    # 3) 中文输出不乱码
    src_cn = write_case("cn.c", """#include <stdio.h>
int main(void) { printf("你好，世界\\n"); return 0; }
""")
    res_cn = run_mod.run_once(src_cn, os.path.join(WORK, "t3"),
                              source_encoding="utf-8", compiler=comp)
    check("中文输出不乱码", "你好，世界" in res_cn.stdout, repr(res_cn.stdout))

    # 4) 死循环必须超时，且不能把测试卡住
    src_loop = write_case("loop.c", """#include <stdio.h>
int main(void) { int i = 0; while (i < 10) { printf("x"); } return 0; }
""")
    res_loop = run_mod.run_once(src_loop, os.path.join(WORK, "t4"), timeout=3,
                                source_encoding="utf-8", compiler=comp)
    check("死循环：超时被终止", res_loop.timed_out and not res_loop.ok)
    check("死循环：提示里点名「死循环」（学生要看得懂）",
          any("死循环" in n for n in res_loop.notes), res_loop.notes)

    # 5) 崩溃要翻译成人话
    src_crash = write_case("crash.c", """#include <stdio.h>
int main(void) { int a[3]; for (int i = 0; i < 200000; i++) a[i] = i; return 0; }
""")
    res_crash = run_mod.run_once(src_crash, os.path.join(WORK, "t5"),
                                 source_encoding="utf-8", compiler=comp)
    check("崩溃：非 0 退出且给出人话解释",
          (not res_crash.ok) and any("内存" in n or "越界" in n or "栈" in n
                                     for n in res_crash.notes),
          res_crash.notes)

    # 6) 编译不过就不运行
    src_bad = write_case("bad.c", "int main(void) { int x = 1 return 0; }\n")
    res_bad = run_mod.run_once(src_bad, os.path.join(WORK, "t6"),
                               source_encoding="utf-8", compiler=comp)
    check("编译不过：不运行，并提示去看报错",
          (not res_bad.compiled) and any("编译" in n for n in res_bad.notes),
          res_bad.notes)

    # 7) 需要输入但没给输入 —— 不能挂死，要给提示
    src_need = write_case("need.c", """#include <stdio.h>
int main(void) { int a; scanf("%d", &a); return 0; }
""")
    res_need = run_mod.run_once(src_need, os.path.join(WORK, "t7"), timeout=5,
                                source_encoding="utf-8", compiler=comp)
    check("需要输入却没给：不挂死，且提示去填输入",
          (not res_need.timed_out) and any("输入" in n for n in res_need.notes),
          res_need.notes)

# ---------------------------------------------------------------- 流式输出与编译缓存
print()
print("— 流式输出与编译缓存 —")
if comp is None:
    print("[SKIP] 本机没有可用编译器")
else:
    import time as _time

    src_slow = write_case("slow.c", """#include <stdio.h>
#include <windows.h>
int main(void) {
    for (int i = 1; i <= 3; i++) { printf("L%d\\n", i); fflush(stdout); Sleep(350); }
    fprintf(stderr, "E1\\n");
    return 0;
}
""")
    stamps = []
    t_start = _time.perf_counter()
    res_slow = run_mod.run_once(src_slow, os.path.join(WORK, "t_stream"), timeout=30,
                                source_encoding="utf-8", compiler=comp,
                                on_output=lambda t: stamps.append(
                                    (_time.perf_counter() - t_start, t)))
    total = _time.perf_counter() - t_start
    joined = "".join(t for _, t in stamps)
    check("流式输出：程序还在跑就已经拿到分批输出", len(stamps) >= 2, len(stamps))
    check("流式输出：第一块明显早于程序结束（不是攒完才给）",
          bool(stamps) and stamps[0][0] < total - 0.3,
          (round(stamps[0][0], 2), round(total, 2)) if stamps else None)
    check("流式输出：分批发的内容拼起来就是完整输出",
          "L1" in joined and "L3" in joined and "L3" in res_slow.stdout,
          joined[:60])
    check("流式输出：stderr 也并进同一股，顺序不乱", "E1" in joined, joined[:60])

    src_loop2 = write_case("loop2.c", """#include <stdio.h>
#include <windows.h>
int main(void) { int i = 0; for (;;) { printf("tick %d\\n", i++); fflush(stdout); Sleep(100); } }
""")
    got = []
    res_to = run_mod.run_once(src_loop2, os.path.join(WORK, "t_stream2"), timeout=2,
                              source_encoding="utf-8", compiler=comp,
                              on_output=got.append)
    check("死循环超时：已经打印出来的内容仍然保留",
          res_to.timed_out and len(got) >= 3 and "tick" in res_to.stdout,
          (res_to.timed_out, len(got)))

    # 缓存：同样的源码第二次不该再编译
    src_q = write_case("cache.c",
                       '#include <stdio.h>\nint main(void){printf("c1\\n");return 0;}\n')
    wd_c = os.path.join(WORK, "t_cache")
    t0 = _time.perf_counter()
    r1 = run_mod.run_once(src_q, wd_c, timeout=10, compiler=comp)
    dt1 = _time.perf_counter() - t0
    t0 = _time.perf_counter()
    r2 = run_mod.run_once(src_q, wd_c, timeout=10, compiler=comp)
    dt2 = _time.perf_counter() - t0
    # 注意：缓存是跨进程留着的，跑第二遍测试时第一次也可能已经命中，
    # 所以这里判"第二次够快"（编译一次 100ms 起步），而不是"比第一次快多少"。
    check("同内容重复运行：第二次不用再编译（够快且输出对）",
          dt2 < 0.25 and r2.stdout.strip() == "c1",
          (round(dt1 * 1000), round(dt2 * 1000)))
    check("同内容重复运行：输出与第一次一致", r1.stdout.strip() == r2.stdout.strip() == "c1",
          (r1.stdout, r2.stdout))
    with open(src_q, "w", encoding="utf-8", newline="\n") as f:
        f.write('#include <stdio.h>\nint main(void){printf("c2\\n");return 0;}\n')
    r3 = run_mod.run_once(src_q, wd_c, timeout=10, compiler=comp)
    check("改了代码必须重新编译：输出跟着变", r3.stdout.strip() == "c2", r3.stdout)

# ---------------------------------------------------------------- 半交互输入
print()
print("— 半交互输入（程序问一句、这边答一句）—")
if comp is None:
    print("[SKIP] 本机没有可用编译器")
else:
    src_ask = write_case("ask.c", """#include <stdio.h>
int main(void) {
    int a = 0, b = 0;
    printf("A?\\n"); fflush(stdout);
    if (scanf("%d", &a) != 1) { printf("读不到第一个数\\n"); return 3; }
    printf("B?\\n"); fflush(stdout);
    if (scanf("%d", &b) != 1) { printf("读不到第二个数\\n"); return 4; }
    printf("sum=%d\\n", a + b);
    return 0;
}
""")
    q = queue.Queue()
    seen = []

    def auto_answer(text):
        seen.append(text)
        if "A?" in text:
            q.put("3\n")
        if "B?" in text:
            q.put("4\n")

    res_ask = run_mod.run_once(src_ask, os.path.join(WORK, "t_ask"), timeout=20,
                               source_encoding="utf-8", compiler=comp,
                               on_output=auto_answer, stdin_queue=q)
    check("半交互：程序问一句、这边答一句，结果正确",
          "sum=7" in res_ask.stdout, res_ask.stdout)
    check("半交互：确实是分两次问、分两次答（不是一次塞完）",
          len(seen) >= 2 and "A?" in seen[0], [t.strip() for t in seen])
    check("半交互：程序读不到数据会提前退出（不会挂到超时）",
          (not res_ask.timed_out) and res_ask.exit_code == 0,
          (res_ask.timed_out, res_ask.exit_code))

    # 送 EOF（None）后，等输入的程序应当立刻结束，而不是等到超时
    src_need2 = write_case("need2.c",
                           "#include <stdio.h>\nint main(void){int a;"
                           "if(scanf(\"%d\", &a)!=1) return 7; return 0;}\n")
    # 这个程序一行输出都没有（直接等输入），所以不能靠"看到输出就送"，
    # 而是在另一个线程里等它真的开始等了，再送 EOF。
    q2 = queue.Queue()
    threading.Timer(0.5, lambda: q2.put(None)).start()
    res_eof = run_mod.run_once(src_need2, os.path.join(WORK, "t_eof"), timeout=20,
                               source_encoding="utf-8", compiler=comp,
                               stdin_queue=q2)
    src_wait = write_case("wait.c", """#include <stdio.h>
int main(void) { int n = 0; printf("输入位数\\n"); fflush(stdout);
                  scanf("%d", &n); printf("%d\\n", n); return 0; }
""")
    q3 = queue.Queue()
    res_wait = run_mod.run_once(src_wait, os.path.join(WORK, "t_wait"), timeout=3,
                                source_encoding="utf-8", compiler=comp,
                                on_output=lambda t: None, stdin_queue=q3)
    check("半交互超时：提示先问是不是在等输入，而不是武断说死循环",
          res_wait.timed_out and any("输入" in n for n in res_wait.notes),
          res_wait.notes)

    check("半交互：送 EOF 后等输入的程序立刻结束",
          (not res_eof.timed_out) and res_eof.exit_code == 7,
          (res_eof.timed_out, res_eof.exit_code))

n_fail = sum(1 for _, ok in results if not ok)
print()
print("=" * 66)
print("运行功能测试：%d 项，通过 %d，失败 %d" % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)
