#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ctutor 命令行入口。

定位（来自评审建议）：**只解决「编译不过、看不懂」，不解决算法、调试、考试。**
凡是帮人读懂报错的功能保留；凡是替人改代码的功能一律不是主路径——
因此 `check` 永远不会修改学生文件，`fix` 是需要显式调用的实验性子命令。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import ai as ai_mod                             # noqa: E402
from engine import (ai_explain, autofix, compilers, config as config_mod,  # noqa: E402
                    explain, fixit, jsonlog, render)
from engine.encoding import detect_source_encoding         # noqa: E402
from engine.kb import KnowledgeBase                         # noqa: E402

ROOT = config_mod.ROOT
TOOL_VERSION = "0.6.2"
REPORT_SCHEMA = "ctutor/report/1"


# ---------------------------------------------------------------- 公共
def _top_errors(cfg):
    path = os.path.join(config_mod.path_of("data", cfg), "top_errors.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _covered_rate(compiler: str, kb: KnowledgeBase, cfg) -> str:
    data = _top_errors(cfg)
    tokens = data.get(compiler) or []
    if not tokens:
        return "（无 Top-20 清单）"
    hit = [t for t in tokens if kb.covers(t)]
    miss = [t for t in tokens if not kb.covers(t)]
    text = "Top-%d 高频命中 %d/%d（%.0f%%）" % (
        len(tokens), len(hit), len(tokens), 100.0 * len(hit) / len(tokens))
    if miss:
        text += "\n        未覆盖：" + "、".join(miss[:8])
        if len(miss) > 8:
            text += " 等 %d 项" % len(miss)
    return text


# ---------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    cfg = config_mod.load()
    config_mod.ensure_dirs(cfg)
    print("ctutor %s　配置：%s" % (TOOL_VERSION, config_mod.config_path()))
    print("-" * 60)
    vc, gc = compilers.find_vcvars(cfg), compilers.find_gcc(cfg)
    print("MSVC   : %s" % (vc if vc else "未找到"))
    print("GCC    : %s" % (gc if gc else "未找到（可放入 bin/w64devkit 后重试）"))
    chosen = compilers.detect(args.compiler, cfg)
    print("将使用 : %s" % (chosen.name if chosen else "无可用编译器"))
    c = cfg.get("compiler") or {}
    print("编译参数: std=%s warnings=%s 超时=%ss 输出上限=%s bytes"
          % (c.get("std"), c.get("warnings"), c.get("timeout_seconds"),
             c.get("max_output_bytes")))
    print("目录   : build=%s" % config_mod.path_of("build", cfg))
    print("         log  =%s" % config_mod.path_of("log", cfg))
    print("         data =%s" % config_mod.path_of("data", cfg))
    print()
    if ai_mod.has_api_key():
        print("AI 层  : 已配置密钥 %s" % ai_mod.mask(ai_mod.find_api_key()))
        print("         来源：%s" % ai_mod.key_source())
    else:
        print("AI 层  : 未配置密钥（--ai 不可用；本地层完全不受影响）")
        print("         配置方式：面板菜单 AI → 设置 DeepSeek 密钥，")
        print("                   或运行 python cli.py set-key")
    if args.ai and ai_mod.has_api_key():
        print("        正在测试连通性…")
        try:
            client = ai_mod.DeepSeekClient()
            text, usage = client.chat(
                [{"role": "user", "content": "请只回答两个字：正常"}],
                json_mode=False, max_tokens=200)
            print("        连通 ✓ 模型 %s，返回 %r" % (client.model, text.strip()[:40]))
            print("        tokens=%s" % {k: v for k, v in usage.items()
                                        if not isinstance(v, dict)})
        except ai_mod.AIError as e:
            print("        连通失败：%s" % e)
    print()
    ex = compilers.detect("msvc", cfg) or chosen
    if ex:
        print("自检：编译一个正常的 hello.c")
        wd = os.path.join(config_mod.path_of("build", cfg), "_doctor")
        os.makedirs(wd, exist_ok=True)
        src = os.path.join(wd, "hello.c")
        with open(src, "w", encoding="utf-8") as f:
            f.write('#include <stdio.h>\nint main(void){ printf("ok\\n"); return 0; }\n')
        res = ex.compile(src, wd)
        print("  返回码 %d，诊断数 %d，SARIF=%s，可执行文件 %s"
              % (res.returncode, len(res.diagnostics), res.sarif_status,
                 os.path.basename(res.exe) if res.exe else "无"))
    return 0


# ---------------------------------------------------------------- set-key
def cmd_set_key(args) -> int:
    """把密钥写进用户目录配置（不是项目目录），这样打包分享项目不会带出密钥。"""
    import getpass
    cur = ai_mod.find_api_key()
    if cur:
        print("当前密钥：%s" % ai_mod.mask(cur))
        print("来源    ：%s" % ai_mod.key_source())
    print()
    print("粘贴 DeepSeek API Key（sk- 开头）。直接回车 = 不加更改，输入 -  = 清除。")
    try:
        key = getpass.getpass("Key: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消")
        return 1
    if not key:
        print("未输入，未做任何更改。")
        return 0
    if key == "-":
        key = ""
    path = config_mod.save_user_config({"ai": {"api_key": key}})
    if key:
        print("已保存：%s" % path)
        print("密钥：%s" % ai_mod.mask(key))
        print()
        print("验证一下：python cli.py doctor --ai")
    else:
        print("已清除密钥。本地解释功能不受影响。")
    return 0


# ---------------------------------------------------------------- kb
def cmd_kb(args) -> int:
    cfg = config_mod.load()
    print("知识库目录：%s" % os.path.join(config_mod.path_of("data", cfg), "kb"))
    print("-" * 60)
    for comp in ("msvc", "gcc"):
        kb = KnowledgeBase.load(comp)
        s = kb.stats()
        print("%-5s 条目 %d 个，覆盖错误码 %d 个，含消息指纹 %d 个"
              % (comp, s["entries"], s["codes"], s["with_pattern"]))
        print("        %s" % _covered_rate(comp, kb, cfg))
    return 0


# ---------------------------------------------------------------- pending
def cmd_pending(args) -> int:
    """待补清单：未命中知识库的错误码按出现次数排序。

    评审建议：「解释正确率记『不确定』条目当待补清单」——用真实使用数据驱动知识库增长，
    比一次性猜 80 条更有效。

    重要修正：日志是历史累积的，**必须先把「现在已经收录了的」过滤掉**，
    否则补完之后清单还在，看起来像没补——会让人重复劳动。
    """
    cfg = config_mod.load()
    rows = jsonlog.pending_summary(cfg)

    # 过滤掉当前知识库已经能解释的码
    kbs = {}
    for name in ("msvc", "gcc"):
        try:
            kbs[name] = KnowledgeBase.load(name)
        except Exception:                                       # noqa: BLE001
            kbs[name] = None

    def covered(code: str) -> bool:
        c = (code or "").strip().upper()
        if not c or c == "(无错误码)":
            return False
        for kb in kbs.values():
            if kb is not None and kb.covers(c):
                return True
        return False

    done = [r for r in rows if covered(r[0])]
    left = [r for r in rows if not covered(r[0])]

    if done:
        print("已有 %d 个错误码被知识库收录（从清单中移除）：%s"
              % (len(done), "、".join(r[0] for r in done)))
        print()

    if not left:
        print("待补清单为空。")
        if done:
            print("（历史上记录过 %d 个未收录码，但都已经补进知识库了）" % len(done))
        print("运行 `python cli.py check 你的代码.c` 之后再来看看新出现的。")
        print("（日志目录：%s）" % config_mod.path_of("log", cfg))
        return 0

    print("待补清单（按出现次数排序，来源：真实使用日志）")
    print("-" * 66)
    for code, count, message in left[: args.limit]:
        print("%-14s ×%-4d %s" % (code, count, (message or "")[:44]))
    print("-" * 66)
    print("共 %d 个未收录错误码。补进 data/kb/msvc.json 或 data/kb/gcc.json 即可。" % len(left))
    return 0


# ---------------------------------------------------------------- check
def _build_report(comp, source, res, chosen, exp, ai_meta, depth) -> dict:
    layers = None
    if exp is not None:
        layers = [{"title": t, "lines": b} for t, b in render.layers(exp)]
    return {
        "schema": REPORT_SCHEMA,
        "tool": {"name": "ctutor", "version": TOOL_VERSION},
        "compiler": comp.name,
        "sarif_status": res.sarif_status,
        "timed_out": res.timed_out,
        "truncated": res.truncated,
        "notes": res.notes,
        "file": source,
        "returncode": res.returncode,
        "diagnostics": [d.to_dict() for d in res.diagnostics],
        "chosen": chosen.to_dict() if chosen else None,
        "explanation": None if exp is None else {
            "matched": exp.matched,
            "title": exp.title,
            "entry_code": (exp.entry or {}).get("code", ""),
            "layers": layers,
            "rendered": render.render(exp, depth=depth),
            "ai": exp.ai,
            "ai_meta": ai_meta,
        },
    }


def cmd_run(args) -> int:
    """编译并运行。编译不过就直接告诉用户去看报错，不运行。"""
    from engine import run as run_mod

    cfg = config_mod.load()
    config_mod.ensure_dirs(cfg)
    source = os.path.abspath(args.file)
    if not os.path.exists(source):
        print("找不到文件：%s" % source, file=sys.stderr)
        return 2

    comp = compilers.detect(args.compiler, cfg)
    if comp is None:
        print("没有找到可用的编译器（MSVC 或 GCC）。", file=sys.stderr)
        return 3

    stdin_text = args.input
    if args.input_file:
        try:
            with open(args.input_file, "r", encoding="utf-8", errors="replace") as f:
                stdin_text = f.read()
        except OSError as e:
            print("读取输入文件失败：%s" % e, file=sys.stderr)
            return 2

    enc = detect_source_encoding(source)
    wd = args.workdir or os.path.join(config_mod.path_of("build", cfg), "run_cli")

    res = run_mod.run_once(source, wd, stdin_text=stdin_text or "",
                           timeout=args.timeout, source_encoding=enc,
                           compiler=comp, cfg=cfg)
    print(run_mod.format_result(res))
    return 0 if res.ok else 1


def cmd_check(args) -> int:
    cfg = config_mod.load()
    config_mod.ensure_dirs(cfg)
    source = os.path.abspath(args.file)
    if not os.path.exists(source):
        print("找不到文件：%s" % source, file=sys.stderr)
        return 2

    comp = compilers.detect(args.compiler, cfg)
    if comp is None:
        print("没有可用的编译器。请放入 bin/w64devkit（GCC）或安装 Visual Studio。",
              file=sys.stderr)
        return 3

    build_root = config_mod.path_of("build", cfg)
    wd = args.workdir or os.path.join(
        build_root, os.path.splitext(os.path.basename(source))[0])
    res = comp.compile(source, wd, std=args.std or None,
                       warnings=(cfg.get("compiler") or {}).get("warnings", True)
                       if args.warnings else False)

    jsonlog.event("check", cfg, file=os.path.basename(source), compiler=comp.name,
                  returncode=res.returncode, n_diags=len(res.diagnostics),
                  sarif=res.sarif_status, timed_out=res.timed_out)

    kb = KnowledgeBase.load(comp.name)
    chosen = explain.choose_diagnostic(res.diagnostics, kb)
    ai_meta = ""
    exp = None

    if chosen is not None:
        exp = explain.build(chosen, kb)
        if args.ai and (args.ai_always or not exp.matched):
            ai_res = ai_explain.enrich(exp, max_retry=(cfg.get("ai") or {}).get("max_retries", 1))
            if ai_res.ok:
                exp.ai = ai_res.payload
                exp.ai_notes = list(ai_res.notes) + list(ai_res.warnings)
                exp.ai_meta = ai_res.summary()
                jsonlog.event("ai_call", cfg, code=chosen.code,
                              attempts=ai_res.attempts, degraded=ai_res.degraded,
                              usage=ai_res.usage)
            else:
                exp.ai_meta = "调用失败：" + ai_res.error
                exp.ai_notes = list(ai_res.warnings)
            ai_meta = exp.ai_meta
        elif args.ai and exp.matched:
            exp.ai_meta = "本地知识库已命中，未调用模型（零成本零延迟）"
            ai_meta = exp.ai_meta
        if not exp.matched:
            jsonlog.unmatched(chosen, cfg, os.path.basename(source), exp.line_text)

    if args.output:
        report = _build_report(comp, source, res, chosen, exp, ai_meta,
                              1 if args.brief else args.depth)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("报告已写入：%s" % os.path.abspath(args.output))

    if args.json:
        report = _build_report(comp, source, res, chosen, exp, ai_meta,
                               1 if args.brief else args.depth)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not res.diagnostics:
        print("=" * 66)
        print("编译通过，没有发现任何错误或警告。")
        print("=" * 66)
        return 0

    for note in res.notes:
        print("（%s）" % note)
    if res.notes:
        print()

    depth = 1 if args.brief else args.depth
    if args.all:
        ordered = sorted(res.diagnostics, key=lambda d: d.sort_key())
    else:
        ordered = [chosen] if chosen else []
        if len(res.diagnostics) > len(ordered):
            print("（共 %d 条诊断，这里只讲最可能相关的一条；加 --all 可全部显示）\n"
                  % len(res.diagnostics))

    for d in ordered:
        e = exp if (exp is not None and d is chosen) else explain.build(d, kb)
        print(render.render(e, depth=depth))
        print()
    return 0


# ---------------------------------------------------------------- fix（实验性）
def _try_deterministic_fix(source: str, comp, wd: str, max_iter: int = 3):
    """迭代应用确定性修复，每次重新编译验证。"""
    os.makedirs(wd, exist_ok=True)
    enc = detect_source_encoding(source)
    with open(source, "rb") as f:
        original = f.read()
    data = original
    log = []

    cur_path = os.path.join(wd, "step0.c")
    with open(cur_path, "wb") as f:
        f.write(data)
    res = comp.compile(cur_path, os.path.join(wd, "c0"))
    diags = res.diagnostics
    if not any(d.is_error for d in diags):
        return False, original, ["当前代码没有编译错误，无需修复。"], diags

    for it in range(1, max_iter + 1):
        changed = False
        try:
            text = data.decode(enc, errors="replace")
        except LookupError:
            enc = "utf-8"
            text = data.decode("utf-8", errors="replace")
        new_text, replaced = autofix.fix_text(text)
        if replaced:
            data = new_text.encode(enc, errors="replace")
            log.append("第 %d 轮：全角标点 %d 处 → 半角（第 %s 行）"
                       % (it, len(replaced), "、".join(str(r[0]) for r in replaced[:6])))
            changed = True

        if not changed:
            chosen = explain.choose_diagnostic(diags)
            if chosen and chosen.fixits:
                r = fixit.apply_fixits_to_bytes(data, chosen.fixits, enc)
                if r.changed:
                    data = r.data
                    log.append("第 %d 轮：应用编译器补丁 %d 处" % (it, r.applied))
                    changed = True
                for s in r.skipped:
                    log.append("          跳过：%s" % s)

        if not changed:
            log.append("第 %d 轮：没有可自动应用的确定性修复。" % it)
            break

        cur_path = os.path.join(wd, "step%d.c" % it)
        with open(cur_path, "wb") as f:
            f.write(data)
        res = comp.compile(cur_path, os.path.join(wd, "c%d" % it))
        diags = res.diagnostics
        if not any(d.is_error for d in diags):
            log.append("第 %d 轮：重新编译通过 ✓" % it)
            return True, data, log, diags

    return False, data, log, diags


def cmd_fix(args) -> int:
    cfg = config_mod.load()
    print("注意：本命令超出工具定位（本工具只负责帮你看懂报错，不负责替你改代码）。")
    print("      它是实验性的确定性修复，且必须通过编译验证才会写回。")
    print()
    source = os.path.abspath(args.file)
    if not os.path.exists(source):
        print("找不到文件：%s" % source, file=sys.stderr)
        return 2
    comp = compilers.detect(args.compiler, cfg)
    if comp is None:
        print("没有可用的编译器。", file=sys.stderr)
        return 3

    wd = os.path.join(config_mod.path_of("build", cfg),
                      os.path.splitext(os.path.basename(source))[0] + "_fix")
    ok, data, log, diags = _try_deterministic_fix(source, comp, wd)

    print("=" * 66)
    for line in log:
        print("  " + line)
    print("-" * 66)
    if not any("全角" in l or "补丁" in l for l in log):
        print("没有找到可以自动应用的修复方案。")
        return 1

    enc = detect_source_encoding(source)
    with open(source, "rb") as f:
        original = f.read()
    diff = fixit.unified_diff_bytes(original, data, os.path.basename(source), enc)
    print("修改预览：")
    for line in diff.splitlines()[:60]:
        print("  " + line)
    print("-" * 66)

    if args.dry_run:
        print("--dry-run：未写入任何文件。")
        return 0 if ok else 1

    if ok:
        target = source if args.in_place else os.path.join(
            wd, os.path.basename(source) + ".fixed.c")
        with open(target, "wb") as f:
            f.write(data)
        jsonlog.event("fix", cfg, file=os.path.basename(source), in_place=args.in_place)
        print("已验证修复有效，写入：%s" % target)
        return 0
    print("修复后仍有编译错误——未通过编译验证的修复不会写回原文件。")
    for d in [x for x in diags if x.is_error][:5]:
        print("  [%s] %s %s" % (d.level, d.code, d.message[:70]))
    return 1


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ctutor",
                                description="C 语言报错解释器（帮你读懂编译错误）")
    p.add_argument("--version", action="version", version="ctutor " + TOOL_VERSION)
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("doctor", help="检查环境与配置")
    d.add_argument("--compiler", default=None, choices=["auto", "msvc", "gcc"])
    d.add_argument("--ai", action="store_true", help="额外测试 DeepSeek 连通性")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("check", help="编译并分层解释报错（绝不修改你的代码）")
    c.add_argument("file")
    c.add_argument("--compiler", default=None, choices=["auto", "msvc", "gcc"])
    c.add_argument("--std", default=None)
    c.add_argument("--all", action="store_true", help="解释全部诊断而非只讲第一条")
    c.add_argument("--depth", type=int, default=render.DEFAULT_DEPTH,
                   choices=[1, 2, 3, 4, 5], help="讲解深度 1-5，默认 4")
    c.add_argument("--brief", action="store_true", help="等价于 --depth 1，只给结论")
    c.add_argument("--json", action="store_true", help="输出 JSON 到 stdout")
    c.add_argument("-o", "--output", default=None,
                   help="把结构化报告写成文件（供界面层/统计使用）")
    c.add_argument("--ai", action="store_true",
                   help="知识库未命中时调用 DeepSeek 补充讲解（需 DEEPSEEK_API_KEY）")
    c.add_argument("--ai-always", action="store_true",
                   help="即使知识库命中也让模型补充（用于对比，会产生费用）")
    c.add_argument("--no-warnings", dest="warnings", action="store_false", default=True)
    c.add_argument("--workdir", default=None)
    c.set_defaults(func=cmd_check)

    k = sub.add_parser("kb", help="查看知识库统计与 Top-20 命中率")
    k.set_defaults(func=cmd_kb)

    r = sub.add_parser("run", help="编译并运行程序（先编译，通过后才运行）")
    r.add_argument("file")
    r.add_argument("--compiler", default=None, choices=["auto", "msvc", "gcc"])
    r.add_argument("--timeout", type=int, default=10, help="运行超时秒数（默认 10）")
    r.add_argument("--input", default=None, help="喂给程序的输入文本（如 scanf 需要的）")
    r.add_argument("--input-file", default=None, help="从文件读取程序输入")
    r.add_argument("--workdir", default=None)
    r.set_defaults(func=cmd_run)

    n = sub.add_parser("pending", help="查看待补清单（未收录的错误码）")
    n.add_argument("--limit", type=int, default=30)
    n.set_defaults(func=cmd_pending)

    sk = sub.add_parser("set-key", help="设置 DeepSeek 密钥（写入用户目录配置）")
    sk.set_defaults(func=cmd_set_key)

    f = sub.add_parser("fix", help="[实验性] 应用确定性修复并重新编译验证")
    f.add_argument("file")
    f.add_argument("--compiler", default=None, choices=["auto", "msvc", "gcc"])
    f.add_argument("--in-place", action="store_true", help="验证通过后直接修改原文件")
    f.add_argument("--dry-run", action="store_true", help="只显示预览，不写文件")
    f.add_argument("--workdir", default=None)
    f.set_defaults(func=cmd_fix)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
