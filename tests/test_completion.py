#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补全功能测试。

两层验证：
  ① 数据完整性（纯逻辑）——标签不重复、类型合法、片段含占位符；
  ② 端到端（离屏渲染）——补全提供者注册成功，并且**真的会弹出候选框**。

第 ② 条是重点：注册成功不等于界面可用。这里通过在编辑器里输入 "pri"、
触发建议、再读取 DOM 里的候选行来验证——这是唯一的端到端证据。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

from engine import completion                                # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


# ---------------------------------------------------------------- ① 数据完整性
print("— ① 补全词条数据 —")
its = completion.items()
st = completion.stats()
check("词条文件可加载且非空", len(its) > 0, len(its))
print("       构成：%s" % st)

labels = [i.get("label") for i in its]
dups = sorted({x for x in labels if labels.count(x) > 1})
check("没有重复标签（重复会让初学者看到两条一样的候选）", not dups, dups)

bad = [i.get("label") for i in its if i.get("kind") not in completion.DEFAULT_KINDS]
check("kind 取值都合法", not bad, bad)

no_doc = [i.get("label") for i in its if not i.get("doc")]
check("每条都有中文说明（补全也是教学机会）", not no_doc, no_doc)

snips = [i for i in its if i.get("kind") == "snippet"]
check("片段有 10 条以上", len(snips) >= 10, len(snips))
check("所有片段的 insert 都含 Tab 占位符", all("${" in (i.get("insert") or "") for i in snips),
      [i["label"] for i in snips if "${" not in (i.get("insert") or "")])

# 教学要点：关键片段必须写成正确范式
scanf_snip = next((i for i in snips if i["label"] == "scanf"), None)
check("scanf 片段带 & （漏 & 是初学者最常见的运行时崩溃原因）",
      scanf_snip is not None and "&" in scanf_snip.get("insert", ""),
      scanf_snip.get("insert") if scanf_snip else None)
for_snip = next((i for i in snips if i["label"] == "for"), None)
check("for 片段用两个分号且带花括号",
      for_snip is not None and for_snip["insert"].count(";") >= 2
      and "{" in for_snip["insert"], for_snip.get("insert") if for_snip else None)

script = completion.build_script()
check("注入脚本含 registerCompletionItemProvider('c')",
      "registerCompletionItemProvider('c'" in script)
check("注入脚本暴露 monaco 句柄（runJavaScript 上下文无法 import）",
      "__ctutor_monaco" in script)
check("注入脚本设置了 fixedOverflowWidgets（否则建议框会被视口裁掉）",
      "fixedOverflowWidgets" in script)

# 转义是条件性的（只有数据里真出现 </ 才需要），所以要用构造数据实测
import tempfile                                            # noqa: E402
_tmp = tempfile.mkdtemp(prefix="ctutor_comp_")
with open(os.path.join(_tmp, "completion.json"), "w", encoding="utf-8") as f:
    json.dump({"items": [{"label": "危险", "kind": "keyword",
                          "doc": "结束标签 </script><script>alert(1)</script>",
                          "detail": "x"}]}, f, ensure_ascii=False)
_evil = completion.build_script({"paths": {"data": _tmp}})
check("数据里的 </script> 被转义，不会提前结束 script 标签",
      "</script><script>" not in _evil and "<\\/script>" in _evil,
      _evil[_evil.find("危险") - 40: _evil.find("危险") + 60] if "危险" in _evil else "")
# HTML 里 script 元素的内容只在遇到 </script 时结束，所以只需转义 "</"；
# 数据里出现开标签 <script> 是无害的（不会终止脚本块）。
check("转义后真正的结束标签只剩脚本自身那一个",
      _evil.count("</script>") == 1, _evil.count("</script>"))
check("数据里的两处 </script> 都已被转义",
      _evil.count("<\\/script>") == 2, _evil.count("<\\/script>"))

# ---------------------------------------------------------------- ② 端到端
print()
print("— ② 端到端：候选框是否真的弹出 —")
try:
    import PySide6                                           # noqa: F401
    from PySide6.QtWidgets import QApplication
    has_qt = True
except Exception as e:                                       # noqa: BLE001
    has_qt = False
    print("[SKIP] 未安装 PySide6：%s" % e)

if not has_qt:
    print("跳过端到端验证")
else:
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
    import panel as panel_mod                                # noqa: E402

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = panel_mod.MainWindow()
    win.resize(1100, 760)
    win.show()
    for _ in range(45):
        app.processEvents()
        time.sleep(0.1)

    check("面板使用 Monaco 编辑器", win.editor.monaco)

    info = {}
    win.editor.probe_completion(lambda i: info.update(i))
    for _ in range(20):
        app.processEvents()
        time.sleep(0.1)
    check("补全提供者注册成功", info.get("registered") is True, info)
    check("注册的词条数与数据文件一致", info.get("count") == len(its),
          "%s vs %s" % (info.get("count"), len(its)))
    check("编辑器实例已被 Monaco 记录（选项才会生效）", info.get("editors", 0) >= 1, info)

    # 输入 pri → 触发建议 → 读 DOM
    win.editor.widget.page().runJavaScript("""
    (function () {
      try {
        const m = window.__ctutor_monaco;
        if (!m) { window.__trig = 'no-handle'; return; }
        const ed = m.editor.getEditors()[0];
        ed.setValue('pri');
        ed.setPosition({lineNumber: 1, column: 4});
        ed.focus();
        ed.trigger('t', 'editor.action.triggerSuggest', {});
        window.__trig = 'sent';
      } catch (e) { window.__trig = 'error:' + e.message; }
    })();
    """)
    for _ in range(25):
        app.processEvents()
        time.sleep(0.1)

    dom = {}
    win.editor.widget.page().runJavaScript("""
    JSON.stringify({
      visible: !!document.querySelector('.suggest-widget.visible'),
      rows: Array.from(document.querySelectorAll('.suggest-widget .monaco-list-row'))
                .map(r => (r.textContent || '').trim())
    })
    """, lambda v: dom.update({"r": v}))
    for _ in range(25):
        app.processEvents()
        time.sleep(0.1)

    d = json.loads(dom.get("r") or "{}")
    check("输入 pri 后候选框可见", d.get("visible") is True, d)
    rows = d.get("rows") or []
    check("候选框里有 printf", any("printf" in r for r in rows), rows)
    check("printf 只出现一条（去掉重复后不再有两条同名候选）",
          sum(1 for r in rows if r.startswith("printf")) == 1, rows)
    print("       候选内容：%s" % rows)


n_fail = sum(1 for _, ok in results if not ok)
print()
print("=" * 66)
print("补全测试：%d 项，通过 %d，失败 %d" % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)
