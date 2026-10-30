# -*- coding: utf-8 -*-
"""sidecar 冒烟测试：直接按 NDJSON 协议驱动，验证扩展依赖的每条链路。

不依赖 VS Code：扩展只要解析同样的 JSON，就一定能跑通。
用法：python tests/test_sidecar.py
"""
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SIDECAR = os.environ.get("CTUTOR_SIDECAR") or os.path.join(PLUGIN, "sidecar", "sidecar.py")
# 默认用当前解释器；验证"包里那份裁剪过的运行时够不够用"时改成包内的 python.exe
PYEXE = os.environ.get("CTUTOR_PY_BIN") or sys.executable

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s  -> %r" % (name, detail))


class Client:
    def __init__(self):
        env = dict(os.environ)
        env["CTUTOR_CACHE"] = os.path.join(PLUGIN, ".cache")
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc = subprocess.Popen(
            [PYEXE, "-X", "utf8", "-u", SIDECAR],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)
        self.events = []
        self.next_id = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stderr(self):
        for raw in self.proc.stderr:
            sys.stderr.write("[sidecar] " + raw.decode("utf-8", "replace"))

    def readline(self, timeout=120):
        box = {}

        def run():
            box["line"] = self.proc.stdout.readline()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout)
        if "line" not in box:
            raise TimeoutError("sidecar 无响应")
        return json.loads(box["line"].decode("utf-8", "replace") or "{}")

    def call(self, method, params=None, collect=0):
        with self._lock:
            self.next_id += 1
            rid = self.next_id
            req = {"id": rid, "method": method, "params": params or {}}
            self.proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            event_lines = []
            deadline = time.time() + 180
            while time.time() < deadline:
                msg = self.readline()
                if msg.get("id") == rid:
                    return msg, event_lines
                if msg.get("event"):
                    event_lines.append(msg)
            raise TimeoutError("等 %s 的应答超时" % method)

    def wait_events(self, run_id, want="run.done", timeout=180):
        got = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.readline()
            if msg.get("event") and msg.get("runId") == run_id:
                got.append(msg)
                if msg["event"] == want:
                    return got
        raise TimeoutError("等 %s 事件超时" % want)

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:                                            # noqa: BLE001
            pass
        try:
            self.proc.wait(5)
        except Exception:                                            # noqa: BLE001
            self.proc.kill()


BROKEN = 'int main(void) {\n    int x = 10\n    return 0;\n}\n'
GOOD = '#include <stdio.h>\nint main(void) {\n    printf("你好\\n");\n    return 0;\n}\n'
INTERACTIVE = ('#include <stdio.h>\nint main(void) {\n'
               '    int n = 0;\n    printf("请输入 n: ");\n    fflush(stdout);\n'
               '    if (scanf("%d", &n) != 1) return 1;\n'
               '    printf("你输入了 %d\\n", n);\n    return 0;\n}\n')


def main():
    c = Client()
    hello_evt = c.readline()
    check("启动后先发 ready 事件", hello_evt.get("event") == "ready", hello_evt)

    resp, _ = c.call("hello", {"compiler": "auto"})
    res = resp.get("result") or {}
    check("hello 返回编译器与知识库统计",
          bool(res.get("compiler")) and res.get("kb", {}).get("gcc", 0) > 0, res)

    resp, _ = c.call("check", {"text": BROKEN, "fileName": "main.c", "compiler": "auto"})
    rep = resp.get("result") or {}
    diags = rep.get("diagnostics") or []
    check("check 能报出错误（不再沉默）", resp.get("ok") and diags and rep.get("returncode"),
          (resp.get("ok"), len(diags), rep.get("returncode")))
    first = diags[0] if diags else {}
    rng = (first.get("range") or {}).get("start") or {}
    check("每条诊断都带 VS Code 能用的 range（0 基行号）",
          isinstance(rng.get("line"), int) and isinstance(rng.get("character"), int), first.get("range"))
    check("诊断带 severity 字段", first.get("severity") == "error", first.get("severity"))
    exp = rep.get("explanation") or {}
    layers = exp.get("layers") or []
    check("check 返回 5 层中文讲解", len(layers) == 5 and exp.get("matched"),
          [l.get("title") for l in layers])
    check("讲解里能找到『改法』", any(l.get("title") == "改法" for l in layers))

    resp, _ = c.call("check", {"text": GOOD, "fileName": "ok.c"})
    rep2 = resp.get("result") or {}
    check("正常代码返回零诊断", rep2.get("returncode") == 0 and not rep2.get("diagnostics"),
          (rep2.get("returncode"), len(rep2.get("diagnostics") or [])))

    resp, _ = c.call("fix", {"text": BROKEN, "fileName": "main.c"})
    fx = resp.get("result") or {}
    check("fix 给出修改过的文本", fx.get("changed") is True and isinstance(fx.get("newText"), str), fx)
    check("fix 说清用了什么办法（可验证）", bool(fx.get("how")) and fx.get("verified") is True, fx.get("how"))
    check("fix 修完后源码确实能编译（含中文注释场景另测）",
          "int x = 10;" in (fx.get("newText") or ""), (fx.get("newText") or "")[:80])

    resp, _ = c.call("fix", {"text": GOOD, "fileName": "ok.c"})
    check("能编译的文件 fix 不动它", (resp.get("result") or {}).get("changed") is False,
          resp.get("result"))

    # ---- 运行 + 半交互输入 ----
    resp, _ = c.call("run", {"text": INTERACTIVE, "fileName": "in.c", "timeout": 10})
    run_id = (resp.get("result") or {}).get("runId")
    check("run 返回 runId", bool(run_id), resp)
    saw_prompt = []
    gave_input = [False]
    done = None
    deadline = time.time() + 120
    while time.time() < deadline:
        msg = c.readline()
        if msg.get("event") == "run.out" and msg.get("runId") == run_id:
            saw_prompt.append(msg.get("text") or "")
            if not gave_input[0] and "请输入" in "".join(saw_prompt):
                c.call("input", {"runId": run_id, "text": "7\n"})
                gave_input[0] = True
        elif msg.get("event") == "run.done" and msg.get("runId") == run_id:
            done = msg.get("result") or {}
            break
    out = "".join(saw_prompt)
    check("运行中提示语先到（没等程序结束才一起吐）", "请输入" in out, out[:120])
    check("半交互：程序收到了我们送的输入", "你输入了 7" in out, out[:200])
    check("运行完成并给出退出码", done is not None and done.get("returncode") == 0, done)
    check("提示语与结果分两次到达（真实时）",
          len([x for x in saw_prompt if x.strip()]) >= 2, saw_prompt[:4])

    resp, _ = c.call("run", {"text": 'int main(void){for(;;);}', "fileName": "spin.c", "timeout": 1})
    rid2 = (resp.get("result") or {}).get("runId")
    done2 = None
    deadline = time.time() + 120
    while time.time() < deadline:
        msg = c.readline()
        if msg.get("event") == "run.done" and msg.get("runId") == rid2:
            done2 = msg.get("result") or {}
            break
    check("死循环会被超时机制掐掉（并说明原因）", done2 is not None and done2.get("timedOut"),
          done2)

    resp, _ = c.call("run", {"text": 'int main(void){ undeclared_call(); }', "fileName": "x.c"})
    rid3 = (resp.get("result") or {}).get("runId")
    done3 = None
    deadline = time.time() + 60
    while time.time() < deadline:
        msg = c.readline()
        if msg.get("event") == "run.done" and msg.get("runId") == rid3:
            done3 = msg.get("result") or {}
            break
    check("编译不过时不运行，并把诊断带回来",
          done3 is not None and done3.get("compiled") is False, done3)

    resp, _ = c.call("stop", {"runId": "不存在的"})
    check("停止一个不存在的运行不会崩", (resp.get("result") or {}).get("stopped") is False, resp)

    resp, _ = c.call("nope")
    check("未知方法返回错误而不是退出", resp.get("ok") is False, resp)

    c.call("cleanup")
    check("cleanup 后进程仍活着", c.proc.poll() is None)

    c.close()
    print()
    print("=" * 66)
    print("sidecar 测试：%d 项，通过 %d，失败 %d" % (PASS + FAIL, PASS, FAIL))
    print("=" * 66)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())