#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打包成可以拷给别人的便携版。

产物结构（解压即用，不需要装 Python、不需要装编译器、不需要管理员权限）：

    ctutor/
      ctutor.exe            ← 双击启动图形面板
      README.md             使用说明
      使用说明.txt           给朋友看的三句话
      data/                 知识库与补全词条（可直接编辑）
      config/               配置示例（密钥写用户目录，不在这里）
      bin/w64devkit/        便携 GCC（自带，不需要对方装编译器）
      _internal/            PyInstaller 运行时（不用管）

为什么要自带编译器：对方机器上大概率没有 MSVC，也没有 gcc。
不带的话他双击之后第一件事就是"检查失败：没有可用的编译器"。

体积说明：w64devkit 解压后 708MB。这里砍掉对「纯 C 教学」无用的部分
（32 位运行时、CMake、C++/Fortran 前端、gdb、文档），砍到约 360MB。
砍完必须重新跑一遍样本集验证 GCC 仍然能编译——见 --verify。

用法：
    python tools/build_package.py                # 完整打包
    python tools/build_package.py --skip-build   # 跳过 PyInstaller（改配置后重打包用）
    python tools/build_package.py --no-toolchain # 不带编译器（体积小很多）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

PACK = os.path.abspath(os.path.join(ROOT, os.pardir, "_pack"))
DIST = os.path.join(PACK, "dist")
WORK = os.path.join(PACK, "build")
APP = os.path.join(DIST, "ctutor")
TOOLCHAIN_SRC = os.path.join(PACK, "w64devkit", "w64devkit")

from engine import config as config_mod                     # noqa: E402

VERSION = "0.6.0"

# ---- w64devkit 瘦身清单（只删对纯 C 教学确定无用的东西）----
DROP_DIRS = [
    "lib32",            # 111 MB：32 位运行时，我们只做 x64
    "share",            # 42 MB：文档/手册/NSIS/CMake 模块
    "libexec/gcc/x86_64-w64-mingw32/16.2.0/install-tools",
    # C++ 标准库头文件：14 MB / 887 个文件，而且是**包内最深的一条路径**
    # （...\include\c++\ext\pb_ds\detail\...，相对 +145 字符）。
    # 纯 C 教学用不到；删掉既省体积，又给 Windows 的 260 字符路径上限腾出空间。
    "lib/gcc/x86_64-w64-mingw32/16.2.0/include/c++",
]
DROP_BIN = [
    "cmake.exe", "ctest.exe", "cpack.exe", "ccmake.exe", "cmcldeps.exe", "dcmake.exe",
    "gdb.exe", "gdbserver.exe", "ccache.exe", "ccache-gcc.exe", "ccache-g++.exe",
    "gfortran.exe", "x86_64-w64-mingw32-gfortran.exe", "i686-w64-mingw32-gfortran.exe",
    "makensis.exe", "aas-sign.exe", "httpd.exe", "wget.exe", "nc.exe", "whois.exe",
    "quilt.exe", "nroff.exe", "ctags.exe", "ninja.exe", "pkg-config.exe",
]
DROP_LIBEXEC = ["cc1plus.exe", "f951.exe", "lto1.exe", "lto-wrapper.exe"]

USAGE_TXT = """ctutor —— 帮你看懂 C 语言编译报错
================================================

【先看这一条】解压到短路径！
------------------------------------------------
请解压到一个**又短又是纯英文**的目录，例如：

    D:\\ctutor

不要放在微信/QQ 的下载目录里，也不要放桌面深处。

原因：Windows 有 260 字符的路径长度上限。这个包里文件很多、层级较深，
路径一长就会出现这类让人完全看不懂的报错：

    Failed to load Python DLL ... LoadLibrary: 找不到指定的模块
    或者双击之后完全没反应

遇到上面这种情况：把整个 ctutor 文件夹移动到 D:\\ctutor 即可。

怎么用
------
1. 双击 ctutor.exe（不用装 Python，也不用装编译器，都打包在里面了）
2. 「新建项目」建一个自己的文件夹（Ctrl+N），或者把一段 C 代码贴进编辑区
3. 按 F5 看报错讲解
4. 按 F9 **在系统命令行窗口里运行**（scanf 可以真正输入）；想不弹窗就用 Shift+F9
5. Ctrl + 滚轮可以放大缩小，Ctrl+0 恢复默认

它会做什么
----------
· **出错的行直接在编辑器里标红**：正在讲的那条是深红＋行号区红点，其它错误是浅红＋橙点
· 只讲**最可能相关的那一条**错误，不会把几十条报错一次糊给你
· 解释分 5 层：结论 → 定位 → 原因 → 改法 → 知识点
· 输入前几个字母就有代码补全（打 pri 选 printf，打 for 直接补出整个循环）
· **能新建项目**：一个文件夹 + main.c，位置自己定，内置 4 个起始模板
· **能直接运行**：
  · F9 = 开一个真正的命令行窗口，程序跑完窗口不会立刻消失（能看清输出）
  · Shift+F9 = 在面板内运行，输出显示在「运行输出」页（有 10 秒超时保护）
  · 程序崩溃会翻译成人话（比如"数组越界""除以 0"），而不是甩一个 3221225477
· 你的原文件不会被修改，检查和运行用的都是工作副本

费用
----
本地知识库命中时**完全不联网、不花钱**。
只有遇到没收录的错误、并且你勾了「AI 补充」时才会联网，一次约 0.0015 元。

想加自己的词条
--------------
· data\\kb\\msvc.json      MSVC 报错的中文解释
· data\\kb\\gcc.json       GCC 报错的中文解释
· data\\completion.json    代码补全词条
直接编辑保存即可，重启程序生效。

出问题怎么办
------------
· 打不开 / 报 Failed to load Python DLL → **路径太长**，移到 D:\\ctutor 再试
· 报"没有可用的编译器" → 确认 bin\\gcc\\bin\\gcc.exe 还在
  （杀毒软件有时会误删，加个白名单）
· 界面全是方块 → 系统缺中文字体，装个"微软雅黑"即可
· 其他异常 → 看 log\\panel_crash.log

许可
----
本程序源码：见项目仓库。
捆绑的 w64devkit（含 GCC）为 GPL 许可，许可证文件：
  bin\\MinGW-w64-runtime-许可证.txt
GCC 运行时库例外（GCC Runtime Library Exception）适用于你用它编译出的程序。
"""


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg))


# 这两个文件故意不依赖我们的 exe ——
# 「路径太长」恰恰会让 exe 根本起不来，那时候只有它们还能跑。
#
# 为什么拆成 bat + ps1：
#   .bat 用 mbcs 写，GBK 里没有 ⚠ / ✓ 这类符号，直接写会编码失败；
#   而 .ps1 用 UTF-8 带 BOM 写，PowerShell 能正确识别，中文和符号都没问题。
PATH_CHECK_BAT = """@echo off\r
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0path_check.ps1"\r
pause\r
"""

PATH_CHECK_PS1 = """# ctutor 路径体检
# 为什么需要它：Windows 默认路径上限 260 字符。超了之后表现极难排查 ——
# 解压时资源管理器会静默跳过文件，运行时则报
#     Failed to load Python DLL ... LoadLibrary: 找不到指定的模块
# 从这句话完全看不出跟"路径太长"有关系。所以专门给一个能独立运行的自检脚本。
$ErrorActionPreference = 'SilentlyContinue'
$p = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host ''
Write-Host 'ctutor 路径检查' -ForegroundColor Cyan
Write-Host ('-' * 56)
Write-Host ('当前目录     : ' + $p)
Write-Host ('目录本身长度 : ' + $p.Length + ' 字符')

$files = Get-ChildItem -LiteralPath $p -Recurse -File
Write-Host ('文件总数     : ' + $files.Count)

$max = $files | Sort-Object { $_.FullName.Length } -Descending | Select-Object -First 1
if ($max) {
    Write-Host ('最长文件路径 : ' + $max.FullName.Length + ' 字符')
    Write-Host ('             (' + $max.FullName.Substring([Math]::Max(0, $p.Length + 1)) + ')')
}
Write-Host ''

if (-not $max) {
    Write-Host '目录是空的 —— 可能解压就失败了，请重新解压。' -ForegroundColor Red
} elseif ($max.FullName.Length -gt 250) {
    Write-Host '路径太长，这就是打不开的原因。' -ForegroundColor Red
    Write-Host ('Windows 上限 260 字符，当前已到 ' + $max.FullName.Length + ' 字符。') -ForegroundColor Red
    Write-Host ''
    Write-Host '解决办法：把整个 ctutor 文件夹剪切到又短又纯英文的目录，例如' -ForegroundColor Yellow
    Write-Host '          D:\\ctutor' -ForegroundColor Yellow
    Write-Host '      然后重新双击 ctutor.exe。' -ForegroundColor Yellow
} else {
    Write-Host '路径长度正常，打不开的原因不是路径太长。' -ForegroundColor Green
    Write-Host '可以检查：log\\panel_crash.log，或杀毒软件是否删掉了 bin 目录。'
}

# 顺带看看关键文件在不在 —— 解压不完整是第二常见的原因
Write-Host ''
Write-Host '关键文件：' -ForegroundColor Cyan
foreach ($rel in @('ctutor.exe', '_internal\\python312.dll', 'bin\\gcc\\bin\\gcc.exe', 'data\\kb\\gcc.json')) {
    $f = Join-Path $p $rel
    if (Test-Path -LiteralPath $f) {
        Write-Host ('  [有] ' + $rel) -ForegroundColor Green
    } else {
        Write-Host ('  [缺] ' + $rel + '   <-- 解压不完整或被杀毒软件删除') -ForegroundColor Red
    }
}
Write-Host ''
"""


def dir_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def mb(n):
    return "%.1f MB" % (n / 1024.0 / 1024.0)


# ---------------------------------------------------------------- 瘦身工具链
def copy_toolchain(dst):
    if not os.path.isdir(TOOLCHAIN_SRC):
        log("找不到 w64devkit：%s" % TOOLCHAIN_SRC)
        return False
    log("复制工具链 → %s" % dst)
    shutil.copytree(TOOLCHAIN_SRC, dst)

    removed = 0
    for rel in DROP_DIRS:
        p = os.path.join(dst, rel)
        if os.path.isdir(p):
            removed += dir_size(p)
            shutil.rmtree(p, ignore_errors=True)
    for name in DROP_BIN:
        p = os.path.join(dst, "bin", name)
        if os.path.isfile(p):
            removed += os.path.getsize(p)
            os.remove(p)
    for name in DROP_LIBEXEC:
        p = os.path.join(dst, "libexec", "gcc", "x86_64-w64-mingw32", "16.2.0", name)
        if os.path.isfile(p):
            removed += os.path.getsize(p)
            os.remove(p)

    log("瘦身掉了 %s，剩余 %s" % (mb(removed), mb(dir_size(dst))))
    return True


# ---------------------------------------------------------------- PyInstaller
def run_pyinstaller():
    log("运行 PyInstaller（首次会比较慢）…")
    spec = os.path.join(ROOT, "packaging", "ctutor.spec")
    cmd = [sys.executable, "-m", "PyInstaller", spec,
           "--noconfirm", "--clean",
           "--distpath", DIST, "--workpath", WORK,
           "--log-level", "WARN"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        log("PyInstaller 失败：")
        print(out[-3000:])
        print(err[-3000:])
        return False
    log("PyInstaller 完成，用时 %.0fs" % (time.time() - t0))
    return True


# ---------------------------------------------------------------- 组装
def assemble(with_toolchain=True):
    os.makedirs(os.path.join(APP, "data"), exist_ok=True)
    os.makedirs(os.path.join(APP, "config"), exist_ok=True)
    os.makedirs(os.path.join(APP, "log"), exist_ok=True)
    os.makedirs(os.path.join(APP, "build"), exist_ok=True)

    src_data = os.path.join(ROOT, "data")
    shutil.copytree(src_data, os.path.join(APP, "data"), dirs_exist_ok=True)
    shutil.copy2(os.path.join(ROOT, "config", "ctutor.example.json"),
                 os.path.join(APP, "config", "ctutor.example.json"))
    for name in ("README.md",):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(APP, name))
    with open(os.path.join(APP, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(USAGE_TXT)

    # 一个不依赖 exe 的自救脚本：exe 因为路径太长打不开时，它仍然能跑
    with open(os.path.join(APP, "检查是否路径太长.bat"), "w", encoding="ascii",
              newline="") as f:
        f.write(PATH_CHECK_BAT)
    with open(os.path.join(APP, "path_check.ps1"), "w", encoding="utf-8-sig",
              newline="") as f:
        f.write(PATH_CHECK_PS1)

    if with_toolchain:
        # 目录名用 bin\gcc 而不是 bin\w64devkit：短 5 个字符。
        # Windows 有 260 字符路径上限，包内每短一个字符，对方能用的余地就大一点。
        dst = os.path.join(APP, "bin", "gcc")
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        copy_toolchain(dst)
        # 许可证必须随分发一起带上
        lic = os.path.join(dst, "COPYING.MinGW-w64-runtime.txt")
        if os.path.exists(lic):
            shutil.copy2(lic, os.path.join(APP, "bin", "MinGW-w64-runtime-许可证.txt"))
    else:
        os.makedirs(os.path.join(APP, "bin"), exist_ok=True)
        with open(os.path.join(APP, "bin", "把编译器放这里.txt"), "w",
                  encoding="utf-8") as f:
            f.write("把 w64devkit 解压到这个目录，使路径形如：\n"
                    "  bin\\gcc\\bin\\gcc.exe\n\n"
                    "下载：https://github.com/skeeto/w64devkit/releases\n"
                    "（如果这台机器上装了 Visual Studio，则无需这一步）\n")
    return True


def clean_runtime():
    """清空包内的 build/ 与 log/，只保留空目录。

    为什么必须在打包前做：verify() 是在包目录里跑 `ctutor.exe --selftest` 的，
    自检会写工作副本（build/panel/untitled.c）、SARIF、运行目录，
    甚至真的编译出一个 a.exe。这些**测试残留**跟着 zip 发出去，
    既暴露了打包过程，也白白增加体积。
    """
    removed = 0
    for name in ("build", "log"):
        d = os.path.join(APP, name)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            continue
        for entry in os.listdir(d):
            p = os.path.join(d, entry)
            try:
                if os.path.isdir(p):
                    removed += dir_size(p)
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    removed += os.path.getsize(p)
                    os.remove(p)
            except OSError:
                pass
    left = []
    for name in ("build", "log"):
        d = os.path.join(APP, name)
        if os.path.isdir(d):
            left.extend("%s/%s" % (name, x) for x in os.listdir(d))
    if left:
        log("⚠ 包内 build/log 仍有残留：%s" % "、".join(left[:6]))
    else:
        log("已清空包内 build/ 与 log/（移除 %s）" % mb(removed))
    return not left


# ---------------------------------------------------------------- 验证
def verify():
    """跑打包出来的 exe，用它的自检模式验证：能启动、Monaco 在、能编译、讲解命中。"""
    exe = os.path.join(APP, "ctutor.exe")
    if not os.path.exists(exe):
        log("找不到 %s" % exe)
        return False
    shot = os.path.join(PACK, "selftest.png")
    jpath = os.path.splitext(shot)[0] + ".json"
    for p in (shot, jpath):
        if os.path.exists(p):
            os.remove(p)
    log("验证打包结果（离屏启动 + 编译一个样本）…")
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu"
    env.pop("CTUTOR_GCC", None)          # 确保用的是自带的 gcc，而不是开发机上配的
    try:
        subprocess.run([exe, "--selftest", shot], cwd=APP, env=env, timeout=420)
    except subprocess.TimeoutExpired:
        log("exe 自检超时")
        return False
    if not os.path.exists(jpath):
        log("没有生成自检报告（%s）" % jpath)
        return False
    with open(jpath, encoding="utf-8") as f:
        data = json.load(f)
    chk = data.get("check") or {}
    print()
    print("  冻结打包 : %s" % data.get("frozen"))
    print("  程序根目录: %s" % data.get("app_root"))
    print("  编辑器   : %s" % data.get("editor"))
    print("  中文字体 : %s" % data.get("font"))
    print("  补全     : %s" % json.dumps(data.get("completion") or {}, ensure_ascii=False))
    print("  自带 gcc : %s" % (data.get("bin_gcc_found") or "未找到"))
    print("  编译器   : %s（SARIF %s）" % (chk.get("compiler"), chk.get("sarif_status")))
    print("  诊断数   : %s　选中 %s 第 %s 行"
          % (chk.get("diagnostics"), chk.get("chosen_code"), chk.get("chosen_line")))
    print("  讲解命中 : %s" % chk.get("explanation_matched"))
    if chk.get("error"):
        print("  自检异常 : %s" % chk["error"][-500:])
    print("  截图     : %s" % shot)
    print()
    ok = (data.get("frozen") and data.get("editor") == "Monaco"
          and (chk.get("diagnostics") or 0) > 0 and chk.get("explanation_matched")
          and not chk.get("error"))
    log("验证 %s" % ("通过 ✓" if ok else "未通过 ✗"))
    return bool(ok)


# ---------------------------------------------------------------- 打包 zip
def make_zip():
    # 文件名刻意用纯 ASCII：中文目录名本身也会在长路径场景下添乱
    name = "ctutor-portable-%s.zip" % VERSION
    out = os.path.join(PACK, name)
    if os.path.exists(out):
        os.remove(out)
    log("压缩中（体积较大，请稍候）…")
    t0 = time.time()
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _dirs, files in os.walk(APP):
            for f in files:
                full = os.path.join(base, f)
                rel = os.path.relpath(full, os.path.dirname(APP))
                z.write(full, rel)
                count += 1
    log("压缩完成：%s（%d 个文件，用时 %.0fs）"
        % (mb(os.path.getsize(out)), count, time.time() - t0))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包成便携版")
    ap.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller")
    ap.add_argument("--no-toolchain", action="store_true", help="不捆绑编译器")
    ap.add_argument("--no-zip", action="store_true", help="不压缩")
    ap.add_argument("--skip-verify", action="store_true", help="跳过验证")
    args = ap.parse_args(argv)

    print("=" * 72)
    print("打包 ctutor %s" % VERSION)
    print("项目：%s" % ROOT)
    print("输出：%s" % PACK)
    print("=" * 72)

    if not args.skip_build:
        if os.path.isdir(APP):
            shutil.rmtree(APP, ignore_errors=True)
        if not run_pyinstaller():
            return 1
    elif not os.path.isdir(APP):
        log("--skip-build 但 %s 不存在" % APP)
        return 1

    assemble(with_toolchain=not args.no_toolchain)
    log("应用体积：%s" % mb(dir_size(APP)))

    # 路径长度体检：Windows 上限 260 字符，对方把包放在深目录里时会直接打不开。
    # 这里报出「包内最长的相对路径」，对方的前缀加上它不能超过 260。
    longest = config_mod.longest_path_under(APP, 400)
    rel = longest - len(APP)
    log("包内最长相对路径：+%d 字符（对方目录前缀 + %d 必须 ≤ 260）" % (rel, rel))
    if rel > 120:
        log("⚠ 相对路径偏长，建议再砍一层目录")
    else:
        log("路径预算：对方目录前缀只要短于 %d 字符就安全" % (260 - rel))

    if not args.skip_verify:
        if not verify():
            log("验证失败——先别急着发出去，看看上面的输出")
            return 2
        # 验证是在**包目录里**跑自检的，会留下工作副本、SARIF、甚至编译出来的 a.exe。
        # 不清理的话这些测试残留会跟着 zip 一起发给别人（踩过一次：包里有 a.exe）。
        clean_runtime()

    if args.no_zip:
        log("已跳过压缩。目录：%s" % APP)
        return 0
    out = make_zip()
    print()
    print("=" * 72)
    print("完成")
    print("  便携目录：%s" % APP)
    print("  分发包  ：%s" % out)
    print("  解压后直接双击 ctutor.exe 即可，对方无需安装任何东西。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
