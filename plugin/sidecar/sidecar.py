# -*- coding: utf-8 -*-
"""ctutor sidecar：给 VS Code 扩展用的常驻内核进程。

为什么不直接调用 cli.py：
  · cli.py 每次冷启动要 200 ms 左右（Python 解释器 + 导入），按保存触发检查太浪费；
  · 扩展要的是"同一份内核、多个功能"，常驻进程只导入一次（约 77 ms）就够。
  · 运行（run）要能一边输出一边接收键盘输入，必须要长连接。

协议：NDJSON（一行一个 JSON）走 stdin/stdout，事件走同一个 stdout。
  → 请求  {"id": 1, "method": "check", "params": {...}}
  ← 应答  {"id": 1, "ok": true, "result": {...}}
  ← 事件  {"event": "run.out", "runId": "r1", "text": "..."}

约定：**stdout 只放协议**，任何调试信息走 stderr，否则会把 JSON 流打脏。
"""
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
# 开发态：engine 在仓库根目录（…/c-tutor/engine）；打包态：engine 被复制到 sidecar/engine。
# 三个候选目录一次找齐，不靠 cwd（VS Code 起动子进程时的 cwd 不可控）。
_CANDS = [
    os.environ.get("CTUTOR_ENGINE_ROOT") or "",
    HERE,                                   # 打包态：sidecar/engine
    os.path.dirname(HERE),                  # 万一被平铺
    os.path.dirname(os.path.dirname(HERE)), # 开发态：仓库根
]
for _cand in _CANDS:
    if _cand and os.path.isdir(os.path.join(_cand, "engine")) and _cand not in sys.path:
        sys.path.insert(0, _cand)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

if not any(os.path.isdir(os.path.join(p, "engine")) for p in sys.path if p):
    sys.stderr.write("[ctutor] 找不到 engine/ 目录，sidecar 无法工作\n")

from engine import autofix as autofix_mod          # noqa: E402
from engine import compilers as compilers_mod     # noqa: E402
from engine import config as config_mod           # noqa: E402
from engine import explain as explain_mod         # noqa: E402
from engine import fixit as fixit_mod             # noqa: E402
from engine import render as render_mod           # noqa: E402
from engine import report as report_mod           # noqa: E402
from engine import run as run_mod                 # noqa: E402
from engine.encoding import col_to_char_index     # noqa: E402
from engine.kb import KnowledgeBase               # noqa: E402

TOOL_VERSION = getattr(report_mod, "TOOL_VERSION", "0.6.3")
CACHE = os.path.abspath(os.environ.get("CTUTOR_CACHE") or
                        os.path.join(tempfile.gettempdir(), "ctutor-plugin-cache"))

_write_lock = threading.Lock()
_check_lock = threading.Lock()          # 串行化编译，避免多个请求抢同一个 build 目录
_runs = {}                              # runId -> {"q": Queue, "stop": Event, "alive": bool}


# ------------------------------------------------------------------ 基础工具
def send(obj):
    """写一行 JSON。必须用字节写，否则 Windows 控制台代码页会把中文打成 ?。"""
    data = json.dumps(obj, ensure_ascii=False) + "\n"
    with _write_lock:
        try:
            sys.stdout.buffer.write(data.encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:                                    # noqa: BLE001
            pass


def log(msg):
    try:
        sys.stderr.write("[ctutor] %s\n" % msg)
        sys.stderr.flush()
    except Exception:                                        # noqa: BLE001
        pass


def _patch_paths():
    """把 build/ log/ 指到缓存目录：插件装在 ~/.vscode/extensions 下，别往里写垃圾。"""
    orig = config_mod.load

    def load(*args, **kwargs):
        c = orig(*args, **kwargs)
        c.setdefault("paths", {})
        c["paths"]["build"] = os.path.join(CACHE, "build")
        c["paths"]["log"] = os.path.join(CACHE, "log")
        return c

    config_mod.load = load
    for d in (CACHE, os.path.join(CACHE, "build"), os.path.join(CACHE, "log")):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def _job_dir(prefix):
    root = tempfile.mkdtemp(prefix=prefix, dir=CACHE)
    return root


def _write_source(root, name, text):
    """把编辑器里的文本落成真实文件——编译器只认文件，不认内存。"""
    safe = os.path.basename(name or "main.c")
    if not safe.lower().endswith(".c"):
        safe = os.path.splitext(safe)[0] + ".c"
    path = os.path.join(root, safe)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _utf16_len(s):
    return len(s.encode("utf-16-le")) // 2


def _pos_of(lines, line_no, col):
    """编译器列号 → VS Code 的 (行, 列)，列按 **UTF-16 code unit** 计。

    这里是唯一一处必须和编辑器对齐的地方：Windows 上 VS Code 的 Position.character
    是 UTF-16 单元数，不是字节、也不是显示列。我们内核里有三种列号模型
    （MSVC 宽 3 / GCC 字节 / GCC 显示宽 2），全部先归一成字符下标，再转 UTF-16。
    """
    idx = max(0, int(line_no or 1) - 1)
    if idx >= len(lines):
        return {"line": max(0, len(lines) - 1), "character": 0}
    text = lines[idx]
    ci = col_to_char_index(text, int(col or 0), "gcc", "utf-8", "byte")
    if ci < 0:
        ci = 0
    ci = min(ci, len(text))
    return {"line": idx, "character": _utf16_len(text[:ci])}


def _token_end(lines, start):
    """把插入符扩成一"词"，这样波浪线框住的是 token 而不是一个点。"""
    line = start["line"]
    if line >= len(lines):
        return start
    text = lines[line]
    # 从 character（UTF-16）反推字符下标：逐个累加比较，行一般不长
    ci, acc = 0, 0
    while ci < len(text) and acc < start["character"]:
        acc += 1 if ord(text[ci]) < 0x10000 else 2
        ci += 1
    j = ci
    while j < len(text) and (text[j].isalnum() or text[j] == "_"):
        j += 1
    if j == ci:                       # 不在词上就往右扩一个字符，至少能看见
        j = min(len(text), ci + 1)
    return {"line": line, "character": _utf16_len(text[:j])}


_SEVERITY = {"error": "error", "fatal error": "error",
             "warning": "warning", "note": "hint", "info": "hint"}


def _decorate(report, text, file_name):
    """给报告补上扩展直接能用的字段：range / severity / file。"""
    lines = text.split("\n")
    for d in report.get("diagnostics") or []:
        start = _pos_of(lines, d.get("line"), d.get("column"))
        end = _token_end(lines, start)
        d["range"] = {"start": start, "end": end}
        d["severity"] = _SEVERITY.get(d.get("level") or "error", "error")
        d["file"] = file_name
    ch = report.get("chosen")
    if ch:
        start = _pos_of(lines, ch.get("line"), ch.get("column"))
        ch["range"] = {"start": start, "end": _token_end(lines, start)}
        ch["severity"] = _SEVERITY.get(ch.get("level") or "error", "error")
        ch["file"] = file_name
    report["fileName"] = file_name
    return report


def _detect(prefer=None):
    cfg = config_mod.load()
    return compilers_mod.detect(prefer or None, cfg), cfg


# ------------------------------------------------------------------ 各方法
def do_hello(params):
    comp, _cfg = _detect(params.get("compiler"))
    stats = {"msvc": 0, "gcc": 0}
    for name in ("msvc", "gcc"):
        try:
            stats[name] = KnowledgeBase.load(name).stats()["entries"]
        except Exception:                                    # noqa: BLE001
            pass
    return {
        "version": TOOL_VERSION,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "compiler": comp.name if comp else None,
        "kb": stats,
        "cache": CACHE,
    }


def do_check(params):
    text = params.get("text") or ""
    name = params.get("fileName") or "main.c"
    root = _job_dir("check-")
    src = _write_source(root, name, text)
    with _check_lock:
        rep = report_mod.check_once(
            src,
            depth=max(1, min(int(params.get("depth") or 5), 5)),
            use_ai=bool(params.get("ai")),
            compiler_prefer=params.get("compiler") or None,
            workdir=os.path.join(root, "build"),
        )
    return _decorate(rep, text, name)


def do_fix(params):
    """确定性修复：编译器补丁 / 全角标点修复 → **重新编译验证** → 通过才返回新文本。"""
    text = params.get("text") or ""
    name = params.get("fileName") or "main.c"
    root = _job_dir("fix-")
    src = _write_source(root, name, text)
    workdir = os.path.join(root, "build")
    with _check_lock:
        comp, _cfg = _detect(params.get("compiler"))
        if comp is None:
            return {"changed": False, "verified": False,
                    "reason": "没有可用的编译器（MSVC 或 GCC），无法验证修复。"}
        res = comp.compile(src, workdir, source_encoding="utf-8")
        if res.ok:
            return {"changed": False, "verified": True, "reason": "当前文件已经能编译通过。"}
        kb = KnowledgeBase.load(comp.name)
        chosen = explain_mod.choose_diagnostic(res.diagnostics, kb)
        fixits = []
        for d in res.diagnostics or []:
            fixits.extend(d.fixits or [])

        # 候选修复，按"越可信越靠前"排：编译器补丁 → 按讲解补分号 → 全角标点
        candidates = []
        if fixits:
            ap = fixit_mod.apply_fixits_to_bytes(text.encode("utf-8"), fixits[:3], "utf-8")
            if ap.changed:
                candidates.append((ap.data.decode("utf-8", errors="replace"),
                                   "编译器给出的修复建议"))
        if chosen is not None:
            for cand, how in _semicolon_variants(text, chosen.line):
                candidates.append((cand, how))
        t2, changes = autofix_mod.fix_text(text)
        if changes:
            candidates.append((t2, "全角标点 / 常见笔误修复"))

        if not candidates:
            title = (chosen and (chosen.code or chosen.message)) or ""
            return {"changed": False, "verified": False,
                    "reason": "这条错误（%s）没有确定性修法，请按讲解手动改。" % (title or "未知"),
                    "hint": _hint_of(chosen, kb)}

        # 每个候选都要**真的编译一次**，通过才算修好——这是"确定性层"的全部意义。
        tried = []
        for cand_text, how in candidates:
            vroot = _job_dir("verify-")
            vsrc = _write_source(vroot, name, cand_text)
            vres = comp.compile(vsrc, os.path.join(vroot, "build"), source_encoding="utf-8")
            if vres.ok:
                return {"changed": True, "verified": True, "how": how, "newText": cand_text,
                        "reason": "已按「%s」修好，并重新编译验证通过。" % how}
            tried.append(how)
        return {"changed": False, "verified": False, "newText": candidates[0][0],
                "how": candidates[0][1], "tried": tried,
                "reason": "试过 %d 种改法都没能编译通过，已放弃修改（不动你的文件）。"
                          % len(candidates),
                "hint": _hint_of(chosen, kb)}


def _semicolon_variants(text, err_line):
    """按中文讲解最常见的结论"上一行末尾补分号"生成候选。

    只改**行尾**，不碰字符串与注释里的内容：若行尾有 // 注释，分号插在注释前。
    """
    lines = text.split("\n")
    out = []
    seen = set()
    for ln in (int(err_line or 0) - 1, int(err_line or 0) - 2):
        if not (0 <= ln < len(lines)):
            continue
        line = lines[ln]
        stripped = line.rstrip()
        if not stripped or stripped.endswith((";", "{", "}", ",", "\\")):
            continue
        cut = len(line)
        pos = line.find("//")
        if pos >= 0:
            cut = pos
        body = line[:cut].rstrip()
        tail = line[cut:].strip() if pos >= 0 else ""
        if not body or body.endswith(";"):
            continue
        new_line = body + ";" + ((" " + tail) if tail else "")
        cand = list(lines)
        cand[ln] = new_line
        fixed = "\n".join(cand)
        if fixed in seen:
            continue
        seen.add(fixed)
        out.append((fixed, "第 %d 行末尾补分号" % (ln + 1)))
    return out


def _hint_of(chosen, kb):
    if not chosen:
        return ""
    try:
        exp = explain_mod.build(chosen, kb)
        for title, body in render_mod.layers(exp):
            if title == "改法":
                return "\n".join(body)
        return ""
    except Exception:                                        # noqa: BLE001
        return ""


def do_run(params):
    run_id = str(params.get("runId") or ("r%d" % (len(_runs) + 1)))
    q = queue.Queue()
    stop = threading.Event()
    _runs[run_id] = {"q": q, "stop": stop, "alive": True}
    thread = threading.Thread(target=_run_worker, args=(run_id, params, q, stop), daemon=True)
    thread.start()
    return {"runId": run_id}


def _run_worker(run_id, params, q, stop):
    try:
        text = params.get("text") or ""
        name = params.get("fileName") or "main.c"
        root = _job_dir("run-")
        src = _write_source(root, name, text)
        timeout = int(params.get("timeout") or 10)
        if timeout <= 0:
            timeout = 86400           # 0 = 不限时（等输入本来就不计时）
        comp, cfg = _detect(params.get("compiler"))

        def on_output(chunk):
            send({"event": "run.out", "runId": run_id, "text": chunk})

        res = run_mod.run_once(
            src, run_mod.unique_workdir(os.path.join(CACHE, "run")),
            timeout=timeout, source_encoding="utf-8", compiler=comp, cfg=cfg,
            on_output=on_output, stdin_queue=q, stop_event=stop)
        payload = {
            "compiled": bool(getattr(res, "compiled", False)),
            "timedOut": bool(getattr(res, "timed_out", False)),
            "stopped": bool(stop.is_set()),
            "returncode": getattr(res, "exit_code", None),
            "exitReason": (res.exit_reason() if hasattr(res, "exit_reason") else ""),
            "duration": round(float(getattr(res, "duration", 0.0) or 0.0), 2),
            "notes": list(getattr(res, "notes", []) or []),
        }
        if not payload["compiled"]:
            payload["diagnostics"] = [
                {"code": d.code, "level": d.level, "message": d.message,
                 "line": d.line, "column": d.column,
                 "range": {"start": _pos_of(text.split("\n"), d.line, d.column),
                           "end": _token_end(text.split("\n"),
                                             _pos_of(text.split("\n"), d.line, d.column))}}
                for d in (getattr(res, "diagnostics", None) or [])]
        send({"event": "run.done", "runId": run_id, "result": payload})
    except Exception:                                        # noqa: BLE001
        send({"event": "run.done", "runId": run_id,
              "result": {"compiled": False, "notes": [traceback.format_exc()[-400:]]}})
    finally:
        entry = _runs.get(run_id)
        if entry:
            entry["alive"] = False


def do_input(params):
    run_id = str(params.get("runId") or "")
    entry = _runs.get(run_id)
    if not entry or not entry["alive"]:
        return {"delivered": False}
    text = params.get("text")
    entry["q"].put(None if text is None else str(text))
    return {"delivered": True}


def do_stop(params):
    run_id = str(params.get("runId") or "")
    entry = _runs.get(run_id)
    if not entry:
        return {"stopped": False}
    entry["stop"].set()
    try:
        entry["q"].put(None)
    except Exception:                                        # noqa: BLE001
        pass
    return {"stopped": True}


def do_cleanup(params):
    """清掉本进程建过的临时目录（扩展在停用/重开时调用）。"""
    removed = 0
    for name in os.listdir(CACHE) if os.path.isdir(CACHE) else []:
        path = os.path.join(CACHE, name)
        if not os.path.isdir(path):
            continue
        if name in ("build", "log", "run"):
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        except Exception:                                    # noqa: BLE001
            pass
    return {"removed": removed}


METHODS = {
    "hello": do_hello,
    "check": do_check,
    "fix": do_fix,
    "run": do_run,
    "input": do_input,
    "stop": do_stop,
    "cleanup": do_cleanup,
}


# ------------------------------------------------------------------ 主循环
def main():
    _patch_paths()
    log("sidecar 就绪 pid=%d cache=%s" % (os.getpid(), CACHE))
    send({"event": "ready", "result": {"version": TOOL_VERSION,
                                       "python": sys.version.split()[0]}})
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:                                    # noqa: BLE001
            log("坏请求：%s" % line[:200])
            continue
        req_id = req.get("id")
        method = req.get("method")
        handler = METHODS.get(method)
        if handler is None:
            send({"id": req_id, "ok": False, "error": "未知方法：%s" % method})
            continue
        try:
            result = handler(req.get("params") or {})
            send({"id": req_id, "ok": True, "result": result})
        except Exception:                                    # noqa: BLE001
            msg = traceback.format_exc()
            log(msg)
            send({"id": req_id, "ok": False, "error": msg.strip().splitlines()[-1]})


if __name__ == "__main__":
    main()