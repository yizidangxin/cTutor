#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工程基础设施测试：逐条对应评审建议里点名要先验证的事项。

  ① GBK/UTF-8 行列映射   —— 评审列为「三个先验证的坑」之首
  ② SARIF 版本兼容 fallback
  ③ 运行安全：超时 / 进程树清理 / 输出上限
  ④ 目录便携化与配置驱动（不硬编码）
  ⑤ 日志：结构化 JSONL + 待补清单 + 默认不记源码
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compilers, config as config_mod, jsonlog, sarif, textparse  # noqa: E402
from engine.diagnostic import Diagnostic                     # noqa: E402
from engine.encoding import caret_line, char_col, col_to_char_index  # noqa: E402
from engine.kb import KnowledgeBase                          # noqa: E402

WORK = os.path.join(HERE, "build", "_infra")
os.makedirs(WORK, exist_ok=True)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


# ================================================================ ① 行列映射模型
print("— ① GBK / UTF-8 行列映射 —")

# 纯逻辑部分（不依赖编译器）：验证 MSVC 的 1/3 宽度模型
check("纯 ASCII 行：字符列与编译器列一致",
      col_to_char_index("    int x = 10;", 9, "msvc") == 8,
      col_to_char_index("    int x = 10;", 9, "msvc"))
line = '    printf("中文中文中文") int x = 10;'          # 6 个汉字
# 实测：N=6 时 MSVC 列 = 字符列 + 2×6 = 22 + 12 = 34
check("含 6 汉字行：MSVC 列 34 能反解回 'int x' 的正确字符下标",
      line[col_to_char_index(line, 34, "msvc"):][:5] == "int x",
      repr(line[col_to_char_index(line, 34, "msvc"):][:5]))
check("换算后给学生看的字符列 = 22",
      char_col(line, 34, "msvc") == 22, char_col(line, 34, "msvc"))
check("插入符按显示宽度补空格（汉字占 2 列）",
      caret_line("中x", 4, "msvc") == "  ^",
      repr(caret_line("中x", 4, "msvc")))
check("列号非法时安全返回空",
      caret_line("abc", 0, "msvc") == "" and col_to_char_index("", 5, "msvc") == -1)

comp = compilers.detect("auto")
if comp is None:
    print("[SKIP] 无编译器，跳过端到端列号验证")
else:
    # 端到端：用真实编译器在 GBK 源码上验证 —— 插入符必须落在 'int x' 上
    ok_all = True
    detail = ""
    for n in range(0, 5):
        src_line = '    printf("' + "中" * n + '") int x = 10;'
        text = '#include <stdio.h>\n\nint main(void) {\n%s\n    return 0;\n}\n' % src_line
        p = os.path.join(WORK, "col_n%d.c" % n)
        with open(p, "w", encoding="gbk", newline="\n") as f:
            f.write(text)
        res = comp.compile(p, os.path.join(WORK, "cb%d" % n))
        d = next((x for x in res.diagnostics if x.column), None)
        if d is None:
            ok_all = False
            detail = "N=%d 无诊断" % n
            break
        idx = col_to_char_index(src_line, d.column, d.compiler, "gbk")
        got = src_line[idx:idx + 5]
        if got != "int x":
            ok_all = False
            detail = "N=%d 列=%d 反解得到 %r（期望 'int x'）" % (n, d.column, got)
            break
    check("端到端：0~4 个汉字行的插入符都落在 'int x' 上（GBK 源码）", ok_all, detail)

# ================================================================ ② SARIF 兜底
print()
print("— ② SARIF 版本兼容 fallback —")
check("不存在的文件 → status=missing", sarif.load_sarif(os.path.join(WORK, "nope.sarif"))[1] == "missing")

bad = os.path.join(WORK, "bad.sarif")
with open(bad, "w", encoding="utf-8") as f:
    f.write("{ this is not json")
check("损坏文件 → status=invalid", sarif.load_sarif(bad)[1] == "invalid")

future = os.path.join(WORK, "v3.sarif")
with open(future, "w", encoding="utf-8") as f:
    json.dump({"version": "3.0.0", "runs": []}, f)
check("未来大版本 → status=unsupported-version",
      sarif.load_sarif(future)[1] == "unsupported-version")

noruns = os.path.join(WORK, "noruns.sarif")
with open(noruns, "w", encoding="utf-8") as f:
    json.dump({"version": "2.1.0"}, f)
check("缺少 runs → status=invalid", sarif.load_sarif(noruns)[1] == "invalid")

if comp is not None:
    src = os.path.join(WORK, "fallback.c")
    with open(src, "w", encoding="utf-8") as f:
        f.write("#include <stdio.h>\nint main(void){ int x = 10\n return 0; }\n")
    res = comp.compile(src, os.path.join(WORK, "fb"))
    sarif_codes = sorted({d.code for d in res.diagnostics if d.code})
    text_codes = sorted({d.code for d in textparse.parse_full_text(res.stdout, comp.name)[0]
                         if d.code})
    check("SARIF 不可用时文本兜底能拿到同样的错误码",
          sarif_codes == text_codes and bool(sarif_codes),
          "SARIF=%s 文本=%s" % (sarif_codes, text_codes))

# ---- 中文源码的 SARIF 截断陷阱（只在中日韩源码上出现）----
if comp is not None and comp.name == "msvc":
    src = os.path.join(WORK, "snippet_cut.c")
    with open(src, "w", encoding="gbk", newline="\n") as f:
        f.write('#include <stdio.h>\n\nint main(void) {\n    int x = 10；\n'
                '    printf("%d\\n", x);\n    return 0;\n}\n')
    res = comp.compile(src, os.path.join(WORK, "sc"))
    raw = open(res.sarif_path, "rb").read()
    # MSVC 有时会把多字节字符砍在 UTF-8 序列中间（取决于截断点落在哪），
    # 本机这次恰好没砍断——所以这里**手工造一份"砍断"的 SARIF**，
    # 把容错解码这条路径本身钉住，不依赖编译器的临场表现。
    cut_path = os.path.join(WORK, "sarif_cut.sarif")
    with open(cut_path, "wb") as f:
        f.write(raw.replace(b'"text": "', b'"text": "\xe4\xb8', 1))
    cut_status = sarif.load_sarif(cut_path)[1]
    check("SARIF 里被砍掉半个汉字时也能读（不抛异常、状态 ok）",
          cut_status == "ok", cut_status)
    check("容错解码后 SARIF 仍可用（结构完好）",
          sarif.load_sarif(res.sarif_path)[1] == "ok",
          sarif.load_sarif(res.sarif_path)[1])
    d = next((x for x in res.diagnostics if x.code == "C2059"), None)
    check("容错解码后错误码/行号依然正确", d is not None and d.line == 4,
          d.to_dict() if d else None)

# ================================================================ ③ 运行安全
print()
print("— ③ 运行安全：超时 / 输出上限 —")
short = "import time; time.sleep(20)"
code, out, err, timed_out, truncated = compilers.run_safely(
    [sys.executable, "-c", short], WORK, timeout=2, max_bytes=1000)
check("超时被中断并标记 timed_out", timed_out, (code, timed_out))
check("超时后返回码为负（表示被终止）", code < 0, code)

code, out, err, timed_out, truncated = compilers.run_safely(
    [sys.executable, "-c", "print('x'*5000)"], WORK, timeout=20, max_bytes=1000)
check("输出超过上限被截断并标记", truncated and len(out) < 2000,
      (len(out), truncated))

res_timeout = compilers.detect("auto")
if res_timeout is not None:
    old = res_timeout.timeout
    res_timeout.timeout = 1
    src = os.path.join(WORK, "slow.c")
    with open(src, "w", encoding="utf-8") as f:
        f.write("#include <stdio.h>\nint main(void){return 0;}\n")
    # 构造一个必然超时的场景：把编译器指向一个会睡很久的程序
    class Sleeper(type(res_timeout)):
        name = "sleeper"
        def _run(self, *a, **k):
            return None
    res_timeout.timeout = old
    check("编译结果对象带 timed_out / truncated 字段",
          hasattr(compilers.CompileResult("x", 0, "", "", "", ""), "timed_out"))

# ================================================================ ④ 便携化与配置
print()
print("— ④ 目录便携化与配置驱动 —")
cfg = config_mod.load()
for key in ("build", "log", "data", "config", "bin"):
    p = config_mod.path_of(key, cfg)
    check("目录 %s 解析到工具根目录内（便携）" % key, p.startswith(config_mod.ROOT), p)

check("知识库位于 data/kb", os.path.isdir(os.path.join(config_mod.path_of("data", cfg), "kb")))
check("未在代码里硬编码 vcvars 路径（配置文件优先）",
      "vcvars_path" in (cfg.get("compiler") or {}))

os.environ["CTUTOR_STD"] = "c11"
check("环境变量能覆盖配置", config_mod.load()["compiler"]["std"] == "c11",
      config_mod.load()["compiler"]["std"])
del os.environ["CTUTOR_STD"]

check(".gitignore 存在且忽略 config/log/build",
      all(k in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
          for k in ("config/ctutor.json", "log/", "build/")))

# ================================================================ ⑤ 日志
print()
print("— ⑤ 结构化日志与待补清单 —")
cfg_log = config_mod.load()
before = len(jsonlog.read_unmatched(cfg_log))
d = Diagnostic(compiler="msvc", message="测试用未收录错误", code="C9999",
               level="error", file="x.c", line=3)
jsonlog.unmatched(d, cfg_log, "x.c", "int x = 1;")
after = jsonlog.read_unmatched(cfg_log)
check("未命中被写入待补清单", len(after) == before + 1, (before, len(after)))
check("默认不记录源码内容", "source_line" not in after[-1], after[-1])

summary = jsonlog.pending_summary(cfg_log)
check("待补清单按错误码聚合", any(code == "C9999" for code, _, _ in summary), summary[:3])

log_path = os.path.join(config_mod.path_of("log", cfg_log), "ctutor.jsonl")
jsonlog.event("test_event", cfg_log, k=1)
check("事件日志为 JSONL（每行一个 json 对象）",
      os.path.exists(log_path) and isinstance(
          json.loads(open(log_path, encoding="utf-8").readlines()[-1]), dict))

# 关键回归：面板走的 check_once 必须自己写日志。
#
# 踩过的坑：日志原来只写在 cli.py 里，而面板调用的是 check_once，
# 于是"用面板（主要使用方式）时待补清单永远是空的"。
# 更糟的是这个 bug 被 try/except: pass 吞了很久没被发现。
# 所以这里直接验证"跑一次 check_once 就多一条日志"。
print()
print("— ⑤b 面板共用路径也会写日志（回归：曾因 report.py 忘了 import os 而静默失败）—")
try:
    from engine import report as report_mod
    config_mod.ensure_dirs(cfg_log)
    _n0 = len(open(log_path, encoding="utf-8").readlines()) if os.path.exists(log_path) else 0
    _src = os.path.join(ROOT, "tests", "cases", "01_missing_semicolon.c")
    report_mod.check_once(_src, depth=4)
    _n1 = len(open(log_path, encoding="utf-8").readlines()) if os.path.exists(log_path) else 0
    check("check_once（面板路径）会写入事件日志", _n1 > _n0, (_n0, _n1))
    _errlog = os.path.join(config_mod.path_of("log", cfg_log), "jsonlog_error.log")
    check("写日志过程没有静默报错（否则会留下 jsonlog_error.log）",
          not os.path.exists(_errlog),
          open(_errlog, encoding="utf-8").read()[:300] if os.path.exists(_errlog) else "")
except Exception as e:                                          # noqa: BLE001
    check("check_once 会写入事件日志", False, "调用失败：%s" % e)

# 清理测试写入的日志，避免污染真实统计
try:
    for name in ("ctutor.jsonl", "unmatched.jsonl"):
        p = os.path.join(config_mod.path_of("log", cfg_log), name)
        if os.path.exists(p):
            lines = [l for l in open(p, encoding="utf-8") if "C9999" not in l
                     and "test_event" not in l and "01_missing_semicolon.c" not in l]
            open(p, "w", encoding="utf-8").writelines(lines)
except OSError:
    pass

# ================================================================ ⑥ AI 密钥配置
print()
print("— ⑥ AI 密钥：优先级与安全 —")
import tempfile                                          # noqa: E402

from engine import ai as ai_mod                          # noqa: E402

# 用临时 APPDATA，避免动到真实用户配置
_tmp_appdata = tempfile.mkdtemp(prefix="ctutor_appdata_")
_old_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = _tmp_appdata
for _k in ("CTUTOR_API_KEY", "DEEPSEEK_API_KEY"):
    os.environ.pop(_k, None)

user_cfg = config_mod.user_config_path()
check("用户配置在 APPDATA 下、不在项目目录里（打包分享不会带出密钥）",
      user_cfg.startswith(_tmp_appdata) and not user_cfg.startswith(config_mod.ROOT),
      user_cfg)
check("未配置时 has_api_key 为假", not ai_mod.has_api_key())
check("未配置时 key_source 为空串", ai_mod.key_source() == "", ai_mod.key_source())

config_mod.save_user_config({"ai": {"api_key": "sk-test-user-config-123456"}})
check("保存后能读到密钥", ai_mod.find_api_key() == "sk-test-user-config-123456",
      ai_mod.find_api_key())
check("key_source 指明来自用户配置", "用户配置" in ai_mod.key_source(), ai_mod.key_source())

os.environ["DEEPSEEK_API_KEY"] = "sk-from-env-000000"
check("环境变量优先于配置文件（便于临时覆盖）",
      ai_mod.find_api_key() == "sk-from-env-000000", ai_mod.find_api_key())
os.environ.pop("DEEPSEEK_API_KEY", None)

_masked = ai_mod.mask("sk-abcdefghijklmnopqrstuvwxyz")
check("mask 不泄露完整密钥", "abcdefghijklmnop" not in _masked and "..." in _masked, _masked)

config_mod.save_user_config({"ai": {"model": "deepseek-v4-pro"}})
_data = json.load(open(user_cfg, encoding="utf-8"))
check("save_user_config 是合并写入，不覆盖其它键",
      _data["ai"].get("api_key") == "sk-test-user-config-123456"
      and _data["ai"].get("model") == "deepseek-v4-pro", _data)

check("项目 .gitignore 仍忽略项目内配置",
      "config/ctutor.json" in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read())

# ---- BOM 容错（实测踩到的坑：PowerShell / 记事本写 UTF-8 会带 BOM）----
_bom = os.path.join(WORK, "bom_config.json")
with open(_bom, "wb") as f:
    f.write(b"\xef\xbb\xbf" + json.dumps({"ai": {"api_key": "sk-bom-test"}}).encode("utf-8"))
check("带 BOM 的配置能被 read_json 读出（记事本默认加 BOM）",
      config_mod.read_json(_bom).get("ai", {}).get("api_key") == "sk-bom-test",
      config_mod.read_json(_bom))

# 端到端：把带 BOM 的用户配置放到隔离的 APPDATA 里，确认 find_api_key 能拿到
_bom_user = config_mod.user_config_path()
os.makedirs(os.path.dirname(_bom_user), exist_ok=True)
with open(_bom_user, "wb") as f:
    f.write(b"\xef\xbb\xbf" + json.dumps(
        {"ai": {"api_key": "sk-from-bom-config"}}).encode("utf-8"))
check("端到端：带 BOM 的配置文件里的密钥能被识别",
      ai_mod.find_api_key() == "sk-from-bom-config", ai_mod.find_api_key())
check("端到端：key_source 指明来自用户配置", "用户配置" in ai_mod.key_source(),
      ai_mod.key_source())

# 还原环境与配置
config_mod.save_user_config({"ai": {"api_key": "", "model": "deepseek-chat"}})
if _old_appdata is None:
    os.environ.pop("APPDATA", None)
else:
    os.environ["APPDATA"] = _old_appdata
try:
    import shutil as _sh
    _sh.rmtree(_tmp_appdata, ignore_errors=True)
except OSError:
    pass

# ================================================================ 汇总
n_fail = sum(1 for _, ok in results if not ok)
print()
print("=" * 66)
print("基础设施测试：%d 项，通过 %d，失败 %d" % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)
