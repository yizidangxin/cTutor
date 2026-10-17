#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""运行功能的测试。

重点是那些**初学者程序每天都会出现**的状态，而不是"能跑通"：
死循环、等输入、输出刷屏、数组越界崩溃、源码带 BOM。
这些情况如果处理错了，表现往往是"结果莫名其妙"而不是报错，必须用测试钉住。
"""
import os
import sys

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
print("— 系统命令行方式 —")
check("GCC 用 UTF-8 控制台（MinGW 的执行字符集是 UTF-8）",
      run_mod.console_encoding("gcc") == "utf-8")
check("MSVC 用 GBK 控制台（不带 /utf-8 时按系统 ANSI 代码页生成字面量）",
      run_mod.console_encoding("msvc") == "gbk")

_bat_dir = os.path.join(WORK, "console_bat")
os.makedirs(_bat_dir, exist_ok=True)
_bat = run_mod.build_console_bat(os.path.join(_bat_dir, "a.exe"), _bat_dir, "gcc")
check("生成了控制台启动脚本", os.path.exists(_bat), _bat)
_bat_txt = open(_bat, encoding="utf-8").read()
check("脚本里设了 chcp 65001（否则 UTF-8 中文输出全乱码）", "chcp 65001" in _bat_txt)
check("脚本用 %~dp0 定位工作目录（不拼路径，避免引号/中文翻车）",
      'cd /d "%~dp0"' in _bat_txt)
check("脚本结尾有 pause（否则窗口一闪而过，学生看不到输出）",
      "pause" in _bat_txt)
# MSVC 的脚本要按 GBK 写，不能是 UTF-8
_bat2 = run_mod.build_console_bat(os.path.join(_bat_dir, "b.exe"), _bat_dir, "msvc")
try:
    open(_bat2, encoding="utf-8").read()
    _utf8_ok = True
except UnicodeDecodeError:
    _utf8_ok = False
check("MSVC 的脚本按 GBK 编码写（在 936 控制台里中文才不会乱）",
      ("chcp" not in open(_bat2, encoding="gbk").read()) and (not _utf8_ok),
      "utf8_readable=%s" % _utf8_ok)

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

n_fail = sum(1 for _, ok in results if not ok)
print()
print("=" * 66)
print("运行功能测试：%d 项，通过 %d，失败 %d" % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)
