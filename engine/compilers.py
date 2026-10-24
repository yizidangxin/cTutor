# -*- coding: utf-8 -*-
"""编译器适配层：MSVC 与 GCC 两个后端，输出统一的诊断列表。

评审建议第 4 条（运行安全）在这里落地：
  - **编译超时**：病态源码（疯狂宏展开等）会让编译器卡死，必须能中断；
  - **输出上限**：编译器输出可能极大，截断以防内存被吃光；
  - **进程树清理**：MSVC 通过 cmd → cl.exe 两层进程启动，只杀 cmd 会留下孤儿 cl.exe，
    因此超时后用 taskkill /T 杀整棵树。

其余实测依据：
  - cl.exe 的诊断走 stdout（stderr 为 0 字节），不要只读 stderr；
  - 链接错误不进 SARIF，必须由文本解析补充；
  - MSVC 的 SARIF 对警告省略 level 字段，要用文本输出回填。
"""
import glob
import os
import shutil
import subprocess
import threading
from typing import List, Optional, Tuple

from . import config as config_mod
from . import sarif as sarif_mod
from . import textparse
from .diagnostic import Diagnostic, dedupe
from .encoding import decode_output, smart_decode

_BENIGN = ("The system cannot find the path specified.",
           "系统找不到指定的路径。")

# 不让子进程开控制台窗口：面板是无窗口的 GUI，cmd / cl / gcc 都是控制台程序，
# 不加这个标志时每次编译都会在屏幕上闪一下黑框（cl 的 fast path 也一样）。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0


def _spawn_flags() -> int:
    return _NO_WINDOW


# ---------------------------------------------------------------- 安全执行
def _kill_tree(pid: int) -> None:
    """杀掉整棵进程树。Windows 上没有进程组信号，用 taskkill /T。"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=10, creationflags=_spawn_flags())
    except Exception:                                  # noqa: BLE001
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=5, creationflags=_spawn_flags())
        except Exception:                              # noqa: BLE001
            pass


def _child_env(workdir: str, base: Optional[dict] = None) -> dict:
    """给子进程一个**确定可写**的临时目录。

    实测踩到的坑：gcc 在受限环境下会直接失败，输出
        Cannot create temporary file in C:\\Users\\...\\Temp\\: Permission denied
    连编译都没开始（返回码 3，零诊断），表现为"工具莫名其妙不报错了"。
    学校机房这类"系统临时目录不可写"的环境同样会中招。

    因此把 TMP / TEMP / TMPDIR 指到工作目录下，而不是依赖系统临时目录。

    base 不为空时以它为底（MSVC 需要带 vcvars 给出的 INCLUDE / LIB / PATH），
    而不是 os.environ。
    """
    tmp = os.path.join(workdir, "_tmp")
    try:
        os.makedirs(tmp, exist_ok=True)
    except OSError:
        tmp = workdir
    env = dict(base) if base is not None else os.environ.copy()
    for key in ("TMP", "TEMP", "TMPDIR"):
        env[key] = tmp
    return env


def _pump_stream(pipe, sink, collected, fallback, max_bytes, state):
    """边读边发：把子进程输出按行解码后交给 sink，同时攒原始字节给返回值。

    为什么要按行切：UTF-8/GBK 的多字节字符不会包含 0x0A，所以按 `\\n` 切开的
    每段都是完整字符，逐段解码不会把汉字劈成两半（半截字节解码出来是乱码）。
    **每次读到数据就把攒下的完整行发出去**——不能等"攒够 8 行"或定时器：
    程序打印一行后开始算东西（或等输入）时，那一行会一直卡在缓冲区里，
    表现出来就是"明明有输出，面板上却空着"。
    """
    buf = b""
    batch = []

    def flush():
        if not batch:
            return
        raw = b"".join(batch)
        del batch[:]
        if state["bytes"] < max_bytes:
            collected.append(raw)
            state["bytes"] += len(raw)
            sink(decode_output(raw, fallback))
        else:
            state["truncated"] = True

    try:
        while True:
            data = pipe.read1(8192)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                batch.append(line + b"\n")
            flush()
        if buf:
            batch.append(buf)
        flush()
    except Exception:                                           # noqa: BLE001
        pass


def _pump_stdin(pipe, queue, encoding: str) -> None:
    """把用户边跑边敲的输入写进子进程的 stdin（半交互）。

    队列里收到 None 表示没有更多输入了，关掉管道（程序读到 EOF）。
    程序提前退出时写管道会抛异常，直接结束——不能让这个线程把界面拖住。
    """
    try:
        while True:
            item = queue.get()
            if item is None:
                break
            pipe.write(item.encode(encoding, errors="replace"))
            pipe.flush()
    except Exception:                                           # noqa: BLE001
        pass
    finally:
        try:
            pipe.close()
        except Exception:                                   # noqa: BLE001
            pass


def run_safely(args: List[str], workdir: str, timeout: int = 30,
               max_bytes: int = 2_000_000, stdin_text: Optional[str] = None,
               stdin_encoding: str = "utf-8",
               base_env: Optional[dict] = None,
               on_output=None, output_fallback: str = "utf-8",
               stdin_queue=None,
               ) -> Tuple[int, bytes, bytes, bool, bool]:
    """执行外部命令，返回 (返回码, stdout, stderr, 是否超时, 是否被截断)。

    stdin_text 不为 None 时，先把文本写进工作目录下的文件，再把该文件重定向为 stdin。

    为什么用文件而不是管道：管道在"子进程不读、父进程一直写"时会写阻塞，
    和 communicate() 的超时处理叠在一起很容易变成难查的死锁。文件重定向没有这个问题，
    而且学生程序要的输入本来就是一次给完的，不需要交互式终端。

    超时后会**连子进程树一起杀掉**（taskkill /F /T）——学生写出死循环是常态，
    不能让面板跟着卡死。

    on_output 不为 None 时改成"边跑边发"：stderr 并进 stdout（保留先后顺序），
    每读到一批完整行就用它回调一次。面板靠这个把输出实时显示出来，而不是
    等程序跑完才一次性刷出来——学生程序里常有 printf 之后等输入/打日志，
    攒到最后再显示，看起来就像"运行很慢、卡住了"。

    stdin_queue 不为 None 时改成"半交互"：stdin 用管道，往里放字符串就送进程序、
    放 None 就表示没有更多输入。面板靠它实现"程序问一句、学生答一句"，
    又不用弹一个 cmd 控制台黑框。
    """
    interactive = stdin_queue is not None
    stdin_fh = None
    if not interactive:
        try:
            if stdin_text is not None:
                path = os.path.join(workdir, "_stdin.txt")
                with open(path, "wb") as f:
                    f.write(stdin_text.encode(stdin_encoding, errors="replace"))
                stdin_fh = open(path, "rb")
        except OSError:
            stdin_fh = None

    streaming = on_output is not None or interactive
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT if streaming else subprocess.PIPE,
                                stdin=subprocess.PIPE if interactive else stdin_fh,
                                cwd=workdir,
                                env=_child_env(workdir, base_env),
                                creationflags=_spawn_flags())
    except OSError as e:
        if stdin_fh:
            stdin_fh.close()
        return -1, str(e).encode("utf-8"), b"", False, False

    writer = None
    if interactive:
        writer = threading.Thread(target=_pump_stdin,
                                  args=(proc.stdin, stdin_queue, stdin_encoding),
                                  name="ctutor-stdin", daemon=True)
        writer.start()

    timed_out = False
    try:
        if streaming:
            chunks: List[bytes] = []
            state = {"bytes": 0, "truncated": False}
            reader = threading.Thread(
                target=_pump_stream,
                args=(proc.stdout, on_output if on_output is not None else (lambda _t: None),
                      chunks, output_fallback, max_bytes, state),
                name="ctutor-output", daemon=True)
            reader.start()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc.pid)
                try:
                    proc.wait(5)
                except Exception:                           # noqa: BLE001
                    pass
                proc.returncode = -9
            reader.join(3)
            out, err = b"".join(chunks), b""
            truncated = bool(state["truncated"])
        else:
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc.pid)
                try:
                    out, err = proc.communicate(timeout=5)
                except Exception:                           # noqa: BLE001
                    out, err = b"", b""
                proc.returncode = -9
            truncated = False
    finally:
        if stdin_fh:
            try:
                stdin_fh.close()
            except OSError:
                pass
        if interactive:
            try:
                stdin_queue.put(None)          # 让写入线程收工、关掉程序的 stdin
            except Exception:                              # noqa: BLE001
                pass
            if writer is not None:
                writer.join(1)

    if not streaming:
        if len(out) > max_bytes:
            out = out[:max_bytes] + "\n...(输出过长，已截断)".encode("utf-8")
            truncated = True
        if len(err) > max_bytes:
            err = err[:max_bytes] + "\n...(输出过长，已截断)".encode("utf-8")
            truncated = True
    return (proc.returncode if proc.returncode is not None else -1,
            out, err, timed_out, truncated)


def find_vcvars(cfg: Optional[dict] = None) -> Optional[str]:
    cfg = cfg or config_mod.load()
    env = os.environ.get("CTUTOR_VCVARS")
    if env and os.path.exists(env):
        return env
    configured = (cfg.get("compiler") or {}).get("vcvars_path")
    if configured and os.path.exists(configured):
        return configured
    patterns = [
        r"C:\Program Files\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat",
        r"C:\Program Files (x86)\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat",
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def find_gcc(cfg: Optional[dict] = None) -> Optional[str]:
    cfg = cfg or config_mod.load()
    env = os.environ.get("CTUTOR_GCC")
    if env and os.path.exists(env):
        return env
    configured = (cfg.get("compiler") or {}).get("gcc_path")
    if configured and os.path.exists(configured):
        return configured
    for name in ("gcc", "x86_64-w64-mingw32-gcc"):
        p = shutil.which(name)
        if p:
            return p
    # 便携工具链：优先 bin/gcc（打包版），兼容 bin/w64devkit（开发目录）。
    # 目录名短一点是有意的——Windows 有 260 字符路径上限。
    root = config_mod.ROOT
    for pat in (os.path.join(root, "bin", "gcc", "bin", "gcc.exe"),
                os.path.join(root, "bin", "w64devkit", "bin", "gcc.exe"),
                os.path.join(root, "bin", "**", "gcc.exe"),
                r"C:\w64devkit\bin\gcc.exe"):
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0]
    return None


class CompileResult:
    def __init__(self, compiler: str, returncode: int, stdout: str, stderr: str,
                 sarif_path: str, workdir: str, exe: str = ""):
        self.compiler = compiler
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.sarif_path = sarif_path
        self.workdir = workdir
        self.exe = exe
        self.diagnostics: List[Diagnostic] = []
        self.sarif_status = "missing"
        self.timed_out = False
        self.truncated = False
        self.notes: List[str] = []

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not any(d.is_error for d in self.diagnostics)


class Compiler:
    name = "base"

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or config_mod.load()
        c = self.cfg.get("compiler") or {}
        self.timeout = int(c.get("timeout_seconds", 30))
        self.max_bytes = int(c.get("max_output_bytes", 2_000_000))
        self.std = c.get("std", "c17")
        self.warnings = bool(c.get("warnings", True))

    def compile(self, source: str, workdir: str, std: Optional[str] = None,
                warnings: Optional[bool] = None) -> CompileResult:
        raise NotImplementedError

    # ---- 公共后处理：SARIF 优先，文本兜底 ----
    def _finalize(self, res: CompileResult) -> CompileResult:
        data, status = sarif_mod.load_sarif(res.sarif_path)
        res.sarif_status = status
        if status == "ok":
            from_sarif = sarif_mod.parse(data, self.name)
            merged = textparse.merge_text_fallback(from_sarif, res.stdout, self.name)
        else:
            # SARIF 缺失 / 损坏 / 版本不支持 → 退回全文本解析，保证功能不中断
            merged, fixits = textparse.parse_full_text(res.stdout, self.name)
            if fixits:
                for d in merged:
                    d.fixits = [f for f in fixits if not d.line or f.start_line == d.line]
            if status != "missing":
                res.notes.append("SARIF %s，已回退到文本解析" % sarif_mod.describe(status))
        res.diagnostics = dedupe(merged)
        if res.timed_out:
            res.diagnostics.insert(0, Diagnostic(
                compiler=self.name, level="fatal error", code="CTUTOR-TIMEOUT",
                message="编译超时（%d 秒），已终止编译器进程" % self.timeout,
                origin="tool"))
            res.notes.append("编译超时")
        if res.truncated:
            res.notes.append("编译器输出被截断")
        return res


class MsvcCompiler(Compiler):
    name = "msvc"

    # vcvars 环境缓存：按 vcvars 路径索引，形如
    #   {vcvars 路径: {"env": {...}, "cl": "C:\\...\\cl.exe"}}，失败时记 None。
    # 进程内只捕获一次：实测 vcvarsall.bat 每次要约 1.8 秒，而 cl 本身编译
    # 一个小文件只要 0.1 秒——以前每次检查都重跑 vcvars，学生按 F5 等的那两秒
    # 几乎全花在"初始化 VS 环境"上，跟编译无关。
    _ENV_CACHE: dict = {}
    # 预热线程和第一次检查可能同时到这儿：都去跑一遍 vcvars 就白等了 1.8 秒。
    _ENV_LOCK = threading.Lock()

    def __init__(self, vcvars: str, cfg: Optional[dict] = None):
        super().__init__(cfg)
        self.vcvars = vcvars

    @staticmethod
    def available(cfg: Optional[dict] = None) -> bool:
        return find_vcvars(cfg) is not None

    # ---------------------------------------------------------------- 快路径
    @classmethod
    def _fast_path(cls, vcvars: str, workdir: str) -> Optional[Tuple[str, dict]]:
        """返回 (cl.exe, 已初始化的环境)；拿不到就返回 None，调用方退回 .bat。

        捕获方式：写一个只跑 vcvars + `set` 的 bat，把环境整份读回来。
        这样就不必每次编译都在 cmd 里重新 call 一遍 vcvars。
        """
        cached = cls._ENV_CACHE.get(vcvars, False)
        if cached is False:                       # 还没试过
            with cls._ENV_LOCK:
                cached = cls._ENV_CACHE.get(vcvars, False)
                if cached is False:
                    cached = cls._capture_env(vcvars, workdir)
                    cls._ENV_CACHE[vcvars] = cached
        if not cached:
            return None
        cl = cached.get("cl") or ""
        if not cl or not os.path.exists(cl):      # VS 被卸载/移动了 → 重新捕获
            cls._ENV_CACHE.pop(vcvars, None)
            return None
        return cl, cached["env"]

    @staticmethod
    def _capture_env(vcvars: str, workdir: str) -> Optional[dict]:
        bat = os.path.join(workdir, "_cutenv.bat")
        marker = "__CTUTOR_ENV_BEGIN__"
        try:
            with open(bat, "w", encoding="mbcs") as f:
                f.write("@echo off\r\n")
                f.write('call "%s" >nul 2>nul\r\n' % vcvars)
                f.write("echo %s\r\n" % marker)
                f.write("set\r\n")
        except (OSError, UnicodeEncodeError):
            return None

        code, raw_out, _raw_err, _to, _tr = run_safely(
            ["cmd", "/c", bat], workdir, timeout=60, max_bytes=1_000_000)
        if code not in (0, 1):                    # vcvars 失败时会留下空环境
            return None
        text = smart_decode(raw_out)
        env, started = {}, False
        for line in text.splitlines():
            if not started:
                if marker in line:
                    started = True
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            # `set` 还会打印 =C:=C:\... 这类"伪变量"，它们不是合法环境名
            if not key or not (key[0].isalpha() or key[0] == "_"):
                continue
            env[key] = value
        if not env:
            return None

        path = ""
        for k, v in env.items():
            if k.upper() == "PATH":
                path = v
                break
        cl = ""
        for d in path.split(os.pathsep):
            cand = os.path.join(d, "cl.exe")
            if d and os.path.exists(cand):
                cl = cand
                break
        if not cl:
            return None
        return {"env": env, "cl": cl}

    def compile(self, source: str, workdir: str, std: Optional[str] = None,
                warnings: Optional[bool] = None) -> CompileResult:
        std = std or self.std
        warnings = self.warnings if warnings is None else warnings
        os.makedirs(workdir, exist_ok=True)
        sarif_path = os.path.join(workdir, "diagnostics.sarif")
        if os.path.exists(sarif_path):
            os.remove(sarif_path)
        exe = os.path.join(workdir, os.path.splitext(os.path.basename(source))[0] + ".exe")

        flags = ["/nologo", "/std:%s" % std, "/diagnostics:caret"]
        if warnings:
            flags.append("/W4")
        flags.append('/experimental:log "%s"' % sarif_path)
        cl_line = 'cl %s "%s"' % (" ".join(flags), source)

        # ---- 快路径：直接带 vcvars 环境调 cl.exe（跳过每次 1.8 秒的环境初始化）----
        fast = self._fast_path(self.vcvars, workdir)
        if fast:
            cl, env = fast
            code, raw_out, raw_err, timed_out, truncated = run_safely(
                [cl, "/nologo", "/std:%s" % std, "/diagnostics:caret"]
                + (["/W4"] if warnings else [])
                + ["/experimental:log", sarif_path, source],
                workdir, self.timeout, self.max_bytes, base_env=env)
            if code != -1:                    # -1 = 进程根本没起来
                return self._finish(code, raw_out, raw_err, timed_out, truncated,
                                    sarif_path, workdir, exe)
            MsvcCompiler._ENV_CACHE.pop(self.vcvars, None)   # 这条路坏了，别再用

        # ---- 兜底：cmd 里 call vcvars 再编译（最稳，但慢）----
        bat = os.path.join(workdir, "_cl.bat")
        with open(bat, "w", encoding="mbcs") as f:
            f.write("@echo off\r\n")
            f.write('call "%s" >nul 2>nul\r\n' % self.vcvars)
            f.write('cd /d "%~dp0"\r\n')
            f.write(cl_line + "\r\n")

        code, raw_out, raw_err, timed_out, truncated = run_safely(
            ["cmd", "/c", bat], workdir, self.timeout, self.max_bytes)
        return self._finish(code, raw_out, raw_err, timed_out, truncated,
                            sarif_path, workdir, exe)

    def _finish(self, code, raw_out, raw_err, timed_out, truncated,
                sarif_path, workdir, exe) -> CompileResult:
        stdout = smart_decode(raw_out)
        stderr = "\n".join(l for l in smart_decode(raw_err).splitlines()
                           if l.strip() and l.strip() not in _BENIGN)
        res = CompileResult(self.name, code, stdout, stderr, sarif_path, workdir,
                            exe if os.path.exists(exe) else "")
        res.timed_out = timed_out
        res.truncated = truncated
        return self._finalize(res)


class GccCompiler(Compiler):
    name = "gcc"

    def __init__(self, gcc: str, cfg: Optional[dict] = None):
        super().__init__(cfg)
        self.gcc = gcc

    @staticmethod
    def available(cfg: Optional[dict] = None) -> bool:
        return find_gcc(cfg) is not None

    def _base_args(self, std: str, warnings: bool) -> List[str]:
        args = ["-std=%s" % std]
        if warnings:
            args += ["-Wall", "-Wextra"]
        args += [
            "-fdiagnostics-color=never",
            "-fdiagnostics-urls=never",
            "-fdiagnostics-text-art-charset=none",   # 默认是 emoji，在 GBK 控制台会乱码
            "-fdiagnostics-column-unit=byte",        # 默认按显示列，中文会让列号错位
            "-fdiagnostics-parseable-fixits",        # 机器可用的修复补丁
        ]
        return args

    def compile(self, source: str, workdir: str, std: Optional[str] = None,
                warnings: Optional[bool] = None) -> CompileResult:
        std = std or self.std
        warnings = self.warnings if warnings is None else warnings
        os.makedirs(workdir, exist_ok=True)
        sarif_path = os.path.join(workdir, "diagnostics.sarif")
        if os.path.exists(sarif_path):
            os.remove(sarif_path)
        exe = os.path.join(workdir, "a.exe")

        base = [self.gcc] + self._base_args(std, warnings)
        variants = [
            # 首选：显式指定 SARIF 输出文件（GCC 14+）
            ["-fno-diagnostics-json-formatting",
             "-fdiagnostics-add-output=sarif:file=%s,version=2.1" % sarif_path],
            # 回退：老版本只有 -fdiagnostics-format
            ["-fdiagnostics-format=sarif-file"],
            # 再回退：不支持 SARIF，只能靠文本
            [],
        ]
        code = -1
        raw_out = raw_err = b""
        timed_out = truncated = False
        for extra in variants:
            args = base + extra + [source, "-o", exe]
            code, raw_out, raw_err, timed_out, truncated = run_safely(
                args, workdir, self.timeout, self.max_bytes)
            text = smart_decode(raw_out) + smart_decode(raw_err)
            if "unrecognized command-line option" in text or "unrecognized option" in text:
                continue
            break

        text = smart_decode(raw_out) + "\n" + smart_decode(raw_err)
        res = CompileResult(self.name, code, text, "", sarif_path, workdir,
                            exe if os.path.exists(exe) else "")
        res.timed_out = timed_out
        res.truncated = truncated
        return self._finalize(res)


def detect(prefer: Optional[str] = None, cfg: Optional[dict] = None) -> Optional[Compiler]:
    """按计划：GCC 优先（匹配教材、避免 MSVC 的 C4996 假错误），MSVC 兜底。"""
    cfg = cfg or config_mod.load()
    prefer = prefer or (cfg.get("compiler") or {}).get("prefer", "auto")

    if prefer in ("auto", "gcc") and GccCompiler.available(cfg):
        return GccCompiler(find_gcc(cfg), cfg)
    if prefer in ("auto", "msvc") and MsvcCompiler.available(cfg):
        return MsvcCompiler(find_vcvars(cfg), cfg)
    # 指定的编译器不可用时，退回另一个可用的，而不是直接失败
    if MsvcCompiler.available(cfg):
        return MsvcCompiler(find_vcvars(cfg), cfg)
    if GccCompiler.available(cfg):
        return GccCompiler(find_gcc(cfg), cfg)
    return None
