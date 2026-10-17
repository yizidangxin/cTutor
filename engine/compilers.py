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
from typing import List, Optional, Tuple

from . import config as config_mod
from . import sarif as sarif_mod
from . import textparse
from .diagnostic import Diagnostic, dedupe
from .encoding import smart_decode

_BENIGN = ("The system cannot find the path specified.",
           "系统找不到指定的路径。")


# ---------------------------------------------------------------- 安全执行
def _kill_tree(pid: int) -> None:
    """杀掉整棵进程树。Windows 上没有进程组信号，用 taskkill /T。"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=10)
    except Exception:                                  # noqa: BLE001
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        except Exception:                              # noqa: BLE001
            pass


def _child_env(workdir: str) -> dict:
    """给子进程一个**确定可写**的临时目录。

    实测踩到的坑：gcc 在受限环境下会直接失败，输出
        Cannot create temporary file in C:\\Users\\...\\Temp\\: Permission denied
    连编译都没开始（返回码 3，零诊断），表现为"工具莫名其妙不报错了"。
    学校机房这类"系统临时目录不可写"的环境同样会中招。

    因此把 TMP / TEMP / TMPDIR 指到工作目录下，而不是依赖系统临时目录。
    """
    tmp = os.path.join(workdir, "_tmp")
    try:
        os.makedirs(tmp, exist_ok=True)
    except OSError:
        tmp = workdir
    env = os.environ.copy()
    for key in ("TMP", "TEMP", "TMPDIR"):
        env[key] = tmp
    return env


def run_safely(args: List[str], workdir: str, timeout: int = 30,
               max_bytes: int = 2_000_000, stdin_text: Optional[str] = None,
               stdin_encoding: str = "utf-8") -> Tuple[int, bytes, bytes, bool, bool]:
    """执行外部命令，返回 (返回码, stdout, stderr, 是否超时, 是否被截断)。

    stdin_text 不为 None 时，先把文本写进工作目录下的文件，再把该文件重定向为 stdin。

    为什么用文件而不是管道：管道在"子进程不读、父进程一直写"时会写阻塞，
    和 communicate() 的超时处理叠在一起很容易变成难查的死锁。文件重定向没有这个问题，
    而且学生程序要的输入本来就是一次给完的，不需要交互式终端。

    超时后会**连子进程树一起杀掉**（taskkill /F /T）——学生写出死循环是常态，
    不能让面板跟着卡死。
    """
    stdin_fh = None
    try:
        if stdin_text is not None:
            path = os.path.join(workdir, "_stdin.txt")
            with open(path, "wb") as f:
                f.write(stdin_text.encode(stdin_encoding, errors="replace"))
            stdin_fh = open(path, "rb")
    except OSError:
        stdin_fh = None

    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=stdin_fh, cwd=workdir, env=_child_env(workdir))
    except OSError as e:
        if stdin_fh:
            stdin_fh.close()
        return -1, str(e).encode("utf-8"), b"", False, False

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:                              # noqa: BLE001
            out, err = b"", b""
        proc.returncode = -9
    finally:
        if stdin_fh:
            try:
                stdin_fh.close()
            except OSError:
                pass

    truncated = False
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

    def __init__(self, vcvars: str, cfg: Optional[dict] = None):
        super().__init__(cfg)
        self.vcvars = vcvars

    @staticmethod
    def available(cfg: Optional[dict] = None) -> bool:
        return find_vcvars(cfg) is not None

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

        bat = os.path.join(workdir, "_cl.bat")
        with open(bat, "w", encoding="mbcs") as f:
            f.write("@echo off\r\n")
            f.write('call "%s" >nul 2>nul\r\n' % self.vcvars)
            f.write('cd /d "%~dp0"\r\n')
            f.write(cl_line + "\r\n")

        code, raw_out, raw_err, timed_out, truncated = run_safely(
            ["cmd", "/c", bat], workdir, self.timeout, self.max_bytes)

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
