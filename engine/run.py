# -*- coding: utf-8 -*-
"""运行学生程序：先编译，通过后再执行，并把输出安全地取回来。

为什么这件事要单独立一个模块、而不是随手 subprocess 一下：

  · **死循环是常态**（`while(1)`、`for(;;)`、忘了 `i++`）——必须能超时，
    并且连子进程树一起杀，否则面板会跟着卡死；
  · **程序可能等输入**（`scanf`）——不给输入就一直挂着，所以要支持一次性喂入；
  · **可能刷屏**（循环里 `printf`）——必须限长，否则内存被吃光；
  · **可能崩溃**——Windows 只给一个巨大的返回码（0xC0000005），
    对初学者毫无意义，必须翻译成人话；
  · **输出编码不确定**——取决于源码是 GBK 还是 UTF-8。

这几条都不是"理论上可能"，是初学者程序每天都会出现的状态。
"""
import itertools
import os
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from . import compilers as compilers_mod
from . import config as config_mod

# Windows 的异常返回码：直接给学生看 3221225477 等于什么都没说
_WIN_EXIT = {
    0xC0000005: ("读写了不该访问的内存", "最常见的是**数组越界**或**用了空指针**。"
                                      "检查下标有没有超出数组长度、指针有没有初始化。"),
    0xC0000094: ("整数除以 0", "检查除法/取余的除数，尤其是 `a % b` 里 b 会不会等于 0。"),
    0xC00000FD: ("栈溢出", "多半是**递归没有终止条件**，或者局部数组开得太大。"),
    0xC0000374: ("堆内存被破坏", "常见于越界写数组、释放后又使用（use after free）。"),
    0xC000013A: ("程序被中断", "运行期间被 Ctrl+C 或任务管理器结束了。"),
    0xC0000409: ("栈缓冲区被破坏", "常见于数组越界写、字符串拷贝超出长度。"),
}

DEFAULT_RUN_TIMEOUT = 10
DEFAULT_MAX_OUTPUT = 200_000


@dataclass
class RunResult:
    compiled: bool = False          # 是否编译通过（编译不过就不运行）
    ok: bool = False                # 编译通过且正常退出（返回码 0）
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False
    duration: float = 0.0
    exe: str = ""
    notes: List[str] = field(default_factory=list)
    diagnostics: List[Any] = field(default_factory=list)

    def exit_reason(self) -> str:
        """把返回码翻译成人话；正常退出返回空串。"""
        if self.timed_out:
            return ""
        if self.exit_code == 0:
            return ""
        code = self.exit_code & 0xFFFFFFFF
        if code in _WIN_EXIT:
            what, how = _WIN_EXIT[code]
            return "程序异常退出：%s（代码 0x%08X）。%s" % (what, code, how)
        if code >= 0x80000000:
            return ("程序异常退出（代码 0x%08X）——多半是数组越界、空指针，"
                    "或者除以 0。" % code)
        return "程序返回了非 0 的退出码 %d。" % self.exit_code


def normalize_encoding(name: str) -> str:
    """把 utf-8-sig 之类**只对文件有意义**的编码名归一化。

    踩过的坑：源码是 UTF-8 带 BOM 时，用 "utf-8-sig" 去编码喂给程序的输入，
    会在 stdin 开头插入 EF BB BF 三个字节，`scanf("%d")` 直接解析失败，
    程序拿到的是未初始化的垃圾值 —— 而界面上完全看不出哪里错了。
    BOM 是文件层面的东西，喂给 stdin / 解释程序输出时都不该带。
    """
    n = (name or "utf-8").strip().lower().replace("_", "-")
    if n in ("utf-8-sig", "utf8-sig"):
        return "utf-8"
    if n in ("utf-16", "utf-16-le", "utf-16-be"):
        return "utf-8"
    return n or "utf-8"


def decode_output(raw: bytes, prefer: str = "utf-8") -> str:
    """解码程序输出。

    顺序很讲究：**先严格试 UTF-8**，失败再按源码编码/GBK 兜底。

    为什么不能"按源码编码优先"：GBK 的字节空间很宽，几乎任何字节序列都能解出**某个**
    结果，所以"GBK 解成功了"根本不是证据。反过来 UTF-8 校验很严，
    一个 GBK 编码的中文串几乎不可能碰巧是合法 UTF-8。所以 UTF-8 严格通过基本就是它。
    （实测：按源码编码优先时，程序输出的中文会变成 תȦ 这样的乱码。）
    """
    if not raw:
        return ""
    prefer = normalize_encoding(prefer)
    order = ["utf-8"]
    for enc in (prefer, "gbk", "cp936", "big5"):
        if enc and enc.lower() not in [o.lower() for o in order]:
            order.append(enc)
    order.append("latin-1")
    for enc in order:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def run_once(source: str, workdir: str, stdin_text: str = "",
             timeout: int = DEFAULT_RUN_TIMEOUT,
             source_encoding: str = "utf-8",
             compiler: Optional[Any] = None,
             cfg: Optional[dict] = None,
             max_output: int = DEFAULT_MAX_OUTPUT) -> RunResult:
    """编译 source；只有编译通过才运行。"""
    cfg = cfg or config_mod.load()
    if compiler is None:
        compiler = compilers_mod.detect(None, cfg)
    if compiler is None:
        r = RunResult()
        r.notes.append("没有找到可用的编译器（MSVC 或 GCC）。")
        return r

    os.makedirs(workdir, exist_ok=True)
    res = compiler.compile(source, workdir)
    result = RunResult(diagnostics=list(res.diagnostics or []), exe=res.exe or "")

    if not res.ok:
        result.compiled = False
        result.notes.append("编译没有通过，所以没有运行。先按 F5 看报错讲解。")
        return result
    result.compiled = True

    exe = res.exe or ""
    if exe and not os.path.isabs(exe):
        exe = os.path.join(workdir, exe)
    if not exe or not os.path.exists(exe):
        # 有些编译器把可执行文件放在工作目录下的固定名字
        for name in ("a.exe", "main.exe", "program.exe"):
            cand = os.path.join(workdir, name)
            if os.path.exists(cand):
                exe = cand
                break
    if not exe or not os.path.exists(exe):
        result.notes.append("编译成功了，但找不到生成的可执行文件。")
        return result
    result.exe = exe

    t0 = time.time()
    code, out, err, timed_out, truncated = compilers_mod.run_safely(
        [exe], workdir, timeout=timeout, max_bytes=max_output,
        stdin_text=stdin_text, stdin_encoding=normalize_encoding(source_encoding))
    result.duration = time.time() - t0
    result.exit_code = code
    result.timed_out = timed_out
    result.truncated = truncated
    result.stdout = decode_output(out, source_encoding)
    result.stderr = decode_output(err, source_encoding)
    result.ok = (not timed_out) and code == 0

    if timed_out:
        result.notes.append(
            "程序运行超过 %d 秒还没结束，已强制终止 —— **多半是死循环**。"
            "检查一下循环条件、循环变量有没有变化（比如忘了 i++）。" % timeout)
    if truncated:
        result.notes.append("输出太长，只保留了前面一部分。")
    if not result.ok and not timed_out:
        reason = result.exit_reason()
        if reason:
            result.notes.append(reason)
    if not result.stdout.strip() and result.ok and not stdin_text:
        result.notes.append("程序正常结束，但没有任何输出。"
                            "如果你用了 scanf 等输入，先在下面的输入框里填数据。")
    return result


_UNIQ_SEQ = itertools.count(1)


def unique_workdir(base: str, keep: int = 6) -> str:
    """为这一次运行准备一个**独立**的工作目录。

    为什么必须独立（这是实测踩出来的坑）：
    上一次运行的程序很可能还在跑 —— 控制台窗口开着、程序还在等 scanf 输入，
    这时它锁着 a.exe。下一次编译写不进这个文件，链接就失败，
    而界面/命令行只会告诉你"编译没有通过"，
    完全看不出真正原因是"上一个窗口还没关"。
    表现就是"有时候能运行，有时候莫名其妙不能运行"。

    命名里带**进程内递增序号**而不是只用时间戳：同一毫秒内连着调两次
    （学生连点两下运行）时间戳会撞车，那就等于没隔离。
    keep 控制保留几个历史目录，避免无限堆积。
    """
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        return base

    path = base
    for _ in range(1000):
        name = "%s_%03d_%d" % (time.strftime("%Y%m%d_%H%M%S"),
                               int(time.time() * 1000) % 1000,
                               next(_UNIQ_SEQ))
        cand = os.path.join(base, name)
        if not os.path.exists(cand):
            path = cand
            break
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return base

    try:
        import shutil
        subs = sorted(d for d in os.listdir(base)
                      if os.path.isdir(os.path.join(base, d)))
        for d in subs[:-keep]:
            # 正在运行的程序会锁着自己的目录，删不掉就跳过，不影响本次运行
            shutil.rmtree(os.path.join(base, d), ignore_errors=True)
    except OSError:
        pass
    return path


def console_encoding(compiler_name: str) -> str:
    """决定系统控制台该用哪个代码页。

    实测差异：MSVC 不带 `/utf-8` 时，字符串字面量按**系统 ANSI 代码页**
    （中文 Windows 上是 GBK）生成；而 MinGW GCC 默认按 **UTF-8**。
    所以同一份 UTF-8 源码，在两家的控制台里表现不同，不能一刀切。

    选错了的表现就是"程序输出中文全是乱码"——而这跟学生的代码毫无关系。
    """
    return "utf-8" if (compiler_name or "").lower() == "gcc" else "gbk"


CONSOLE_BAT = """@echo off
title ctutor - running your program
{chcp}cd /d "%~dp0"
echo.
"{exe}"
echo.
echo [程序已结束] 按任意键关闭这个窗口...
pause >nul
"""


def build_console_bat(exe: str, workdir: str, compiler_name: str = "msvc") -> str:
    """生成一个用于在系统命令行里运行程序的 .bat，返回它的路径。

    为什么用 .bat 而不是直接 Popen(["cmd","/c", "prog.exe & pause"])：
    cmd 的引号转义规则极其容易出错（路径带空格、带中文时必翻车）。
    写成文件就完全不用拼命令行，而且 `cd /d "%~dp0"` 让工作目录自动等于 exe 所在目录。

    为什么必须有 pause：程序跑完窗口会立刻消失，学生根本来不及看输出——
    这是初学者用 IDE 时最常见的困惑之一。
    """
    enc = console_encoding(compiler_name)
    # chcp 放在**第一行**：cmd 是逐行读取并执行的，之后的正文按新代码页解释，
    # 所以整份文件要按目标代码页写。中文提示因此不会变成乱码。
    chcp = "chcp 65001 >nul\n" if enc == "utf-8" else ""
    body = CONSOLE_BAT.format(chcp=chcp, exe=exe)
    path = os.path.join(workdir, "_run_console.bat")
    with open(path, "w", encoding="utf-8" if enc == "utf-8" else "gbk",
              errors="replace", newline="\r\n") as f:
        f.write(body)
    return path


def run_in_console(source: str, workdir: str, compiler: Optional[Any] = None,
                   cfg: Optional[dict] = None) -> RunResult:
    """在**系统命令行窗口**里运行程序。

    与 run_once 的区别：不捕获输出，而是开一个真正的控制台。
    这样 `scanf` 是真正交互式的——学生能看到提示语、能现场输入，
    和他们在书上/课上学到的行为完全一致。

    仍然先编译：编译不过就不开窗口（否则窗口一闪而过，什么也看不到）。
    """
    import subprocess

    cfg = cfg or config_mod.load()
    if compiler is None:
        compiler = compilers_mod.detect(None, cfg)
    if compiler is None:
        r = RunResult()
        r.notes.append("没有找到可用的编译器（MSVC 或 GCC）。")
        return r

    os.makedirs(workdir, exist_ok=True)
    res = compiler.compile(source, workdir)
    result = RunResult(diagnostics=list(res.diagnostics or []), exe=res.exe or "")
    if not res.ok:
        result.compiled = False
        result.notes.append("编译没有通过，所以没有运行。先按 F5 看报错讲解。")
        return result
    result.compiled = True

    exe = res.exe or ""
    if exe and not os.path.isabs(exe):
        exe = os.path.join(workdir, exe)
    if not exe or not os.path.exists(exe):
        for name in ("a.exe", "main.exe", "program.exe"):
            cand = os.path.join(workdir, name)
            if os.path.exists(cand):
                exe = cand
                break
    if not exe or not os.path.exists(exe):
        result.notes.append("编译成功了，但找不到生成的可执行文件。")
        return result
    result.exe = exe

    bat = build_console_bat(exe, workdir, compiler.name)
    try:
        # CREATE_NEW_CONSOLE：给程序一个属于自己的控制台窗口。
        # 不加这个的话，从图形程序里启动的控制台子进程没有终端可交互，scanf 会立刻失败。
        subprocess.Popen(["cmd.exe", "/c", bat], cwd=workdir,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                         close_fds=True)
    except OSError as e:
        result.notes.append("无法打开系统命令行窗口：%s" % e)
        return result

    result.ok = True
    result.notes.append("已在系统命令行窗口里运行（可以交互输入）。")
    return result


def format_result(r: RunResult) -> str:
    """把运行结果排成给人看的文本（命令行与面板共用，保证两边一致）。"""
    lines = []
    for n in r.notes:
        lines.append("· " + n)
    if lines:
        lines.append("")

    if not r.compiled:
        lines.append("编译未通过，未运行。")
        return "\n".join(lines).strip()

    header = "运行结果"
    if r.ok:
        header += "（正常结束，返回码 0，用时 %.2f 秒）" % r.duration
    elif r.timed_out:
        header += "（超时被强制终止）"
    else:
        header += "（返回码 %d，用时 %.2f 秒）" % (r.exit_code, r.duration)
    lines.append("== " + header + " ==")
    lines.append("")

    body = r.stdout
    if body and not body.endswith("\n"):
        body += "\n"
    lines.append(body if body.strip() else "(没有任何输出)")
    if r.stderr.strip():
        lines.append("")
        lines.append("== 错误输出 ==")
        lines.append(r.stderr)
    return "\n".join(lines)
