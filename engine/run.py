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
import hashlib
import itertools
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from . import compilers as compilers_mod
from . import config as config_mod
from .encoding import decode_output

# Windows 的异常返回码：直接给学生看 3221225477 等于什么都没说
_WIN_EXIT = {
    0xC0000005: ("读写了不该访问的内存", "最常见的是数组越界或用了空指针。"
                                      "检查下标有没有超出数组长度、指针有没有初始化。"),
    0xC0000094: ("整数除以 0", "检查除法/取余的除数，尤其是 `a % b` 里 b 会不会等于 0。"),
    0xC00000FD: ("栈溢出", "多半是递归没有终止条件，或者局部数组开得太大。"),
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
    """把 utf-8-sig 之类只对文件有意义的编码名归一化。

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


def _locate_exe(res, workdir: str) -> str:
    """找出编译产物。编译成功但找不到 exe 是真实会发生的（不同编译器命名习惯）。

    以前面板运行和命令行运行各写了一份；现在只跑面板这一条路，仍保留成函数，
    免得以后再有人复制一遍。
    """
    exe = res.exe or ""
    if exe and not os.path.isabs(exe):
        exe = os.path.join(workdir, exe)
    if not exe or not os.path.exists(exe):
        for name in ("a.exe", "main.exe", "program.exe"):
            cand = os.path.join(workdir, name)
            if os.path.exists(cand):
                return cand
    return exe if exe and os.path.exists(exe) else ""


def _cache_key(source: str, compiler, cfg: dict) -> str:
    """按"源码内容 + 编译器 + 关键编译选项"算一个缓存键。

    为什么要按内容而不是按文件名：学生改了代码就得重新编译，没改就完全不用——
    每次按 F9 都重新 cl 一遍（实测 0.13 秒），再加上刚生成的 exe 第一次执行
    还要被杀软扫一遍（0.06~0.3 秒），重复按 F9 就明显"卡一下"。
    """
    h = hashlib.sha1()
    try:
        with open(source, "rb") as f:
            h.update(f.read())
    except OSError:
        return ""
    # 目录也要算进键里：两份内容完全相同、但放在不同文件夹的代码，可能各自
    # include 同名的本地头文件，编出来的程序并不一样。
    h.update(os.path.dirname(os.path.abspath(source)).encode("utf-8"))
    c = cfg.get("compiler") or {}
    h.update(("%s|%s|%s|%s" % (
        getattr(compiler, "name", "?"),
        getattr(compiler, "vcvars", "") or getattr(compiler, "gcc", ""),
        c.get("std", "c17"), bool(c.get("warnings", True)))).encode("utf-8"))
    return h.hexdigest()[:16]


def _cache_exe(cache_dir: str, key: str, exe: str) -> str:
    """把刚编译出来的 exe 存进缓存目录，返回缓存里的路径。"""
    if not key or not exe:
        return ""
    d = os.path.join(cache_dir, key)
    try:
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, os.path.basename(exe))
        shutil.copy2(exe, dst)
        return dst
    except OSError:
        return ""


def _prune_cache(cache_dir: str, keep: int = 24) -> None:
    """缓存只留最近若干份，免得学生天天改代码把磁盘堆满。"""
    try:
        subs = [os.path.join(cache_dir, n) for n in os.listdir(cache_dir)]
        subs = [p for p in subs if os.path.isdir(p)]
        for p in sorted(subs, key=lambda x: os.path.getmtime(x))[:-keep]:
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def run_once(source: str, workdir: str, stdin_text: str = "",
             timeout: int = DEFAULT_RUN_TIMEOUT,
             source_encoding: str = "utf-8",
             compiler: Optional[Any] = None,
             cfg: Optional[dict] = None,
             max_output: int = DEFAULT_MAX_OUTPUT,
             on_output=None, stdin_queue=None) -> RunResult:
    """编译 source；只有编译通过才运行。

    on_output 不为 None 时把程序输出**实时**回调出去（面板用它边跑边显示），
    stdin_queue 不为 None 时改成半交互：往队列里放字符串就是给程序送一行输入。

    两者都不给时和以前完全一样：一次性喂 stdin、等程序结束再返回。
    """
    cfg = cfg or config_mod.load()
    if compiler is None:
        compiler = compilers_mod.detect(None, cfg)
    if compiler is None:
        r = RunResult()
        r.notes.append("没有找到可用的编译器（MSVC 或 GCC）。")
        return r

    os.makedirs(workdir, exist_ok=True)

    # ---- 源码没变就直接用上次编译出来的 exe ----
    cache_dir = os.path.join(config_mod.path_of("build"), "run_cache")
    key = _cache_key(source, compiler, cfg)
    cached = os.path.join(cache_dir, key, os.path.basename(
        os.path.splitext(source)[0] + ".exe")) if key else ""
    if cached and os.path.exists(cached):
        result = RunResult()
        result.compiled = True
        result.exe = cached
    else:
        res = compiler.compile(source, workdir)
        result = RunResult(diagnostics=list(res.diagnostics or []), exe=res.exe or "")
        if not res.ok:
            result.compiled = False
            result.notes.append("编译没有通过，所以没有运行。先按 F5 看报错讲解。")
            return result
        result.compiled = True
        stored = _cache_exe(cache_dir, key, result.exe)
        if stored:
            result.exe = stored
            _prune_cache(cache_dir)

    exe = result.exe
    if not exe or not os.path.exists(exe):
        exe = _locate_exe(result, workdir)
    if not exe:
        result.notes.append("编译成功了，但找不到生成的可执行文件。")
        return result
    result.exe = exe

    t0 = time.time()
    code, out, err, timed_out, truncated = compilers_mod.run_safely(
        [exe], workdir, timeout=timeout, max_bytes=max_output,
        stdin_text=None if stdin_queue is not None else stdin_text,
        stdin_encoding=normalize_encoding(source_encoding),
        on_output=on_output, output_fallback=source_encoding,
        stdin_queue=stdin_queue)
    result.duration = time.time() - t0
    result.exit_code = code
    result.timed_out = timed_out
    result.truncated = truncated
    result.stdout = decode_output(out, source_encoding)
    result.stderr = decode_output(err, source_encoding)
    result.ok = (not timed_out) and code == 0

    if timed_out:
        if stdin_queue is not None:
            # 半交互模式下"卡住"最常见的原因不是死循环，而是程序停在 scanf 上
            # 等人输入。这种情况说成"死循环"会把学生带偏，必须先讲输入。
            result.notes.append(
                "程序运行超过 %d 秒还没结束，已强制终止。"
                "先看是不是停在 scanf / gets 上等你输入：在输入框里输入后回车"
                "（程序问几行就答几行），输完了点「结束输入」等它跑完。"
                "如果它根本不需要输入，那要么是死循环（检查循环条件和 i++），"
                "要么是这段程序本来就慢（比如循环一亿次）——把工具栏上的"
                "超时秒数调大再跑。" % timeout)
        else:
            result.notes.append(
                "程序运行超过 %d 秒还没结束，已强制终止 —— 多半是死循环。"
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
    """为这一次运行准备一个独立的工作目录。

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
