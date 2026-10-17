# -*- coding: utf-8 -*-
"""C 语言补全词条：数据加载与 Monaco 注入脚本生成。

为什么要把补全做成「数据 + 注入」而不是写死在 JS 里：
  - 词条是可编辑的资产（和知识库一样），老师可以自己加；
  - 注入脚本通过覆盖 Monaco._load_editor() 实现，不改动 qtmonaco 这个第三方包，
    升级依赖不会被覆盖。

注入原理（实测得出，见 tools/_probe_completion*.py）：
  qtmonaco 打包的 index.js 里只为 "python" 硬编码注册了补全提供者，且
  MonacoEnvironment.globalAPI = false（window.monaco 不存在）。但 monaco-core.js
  作为独立 ES 模块可从页面内再次 import，其导出中的 `r` / `m` 就是完整的 monaco
  命名空间，因此可以自行 registerCompletionItemProvider('c', ...)。
"""
import json
import os
from typing import Any, Dict, List

from . import config as config_mod

DEFAULT_KINDS = {"keyword": 17, "function": 1, "snippet": 27,
                 "struct": 6, "type": 5, "value": 13}


def data_path(cfg: Dict[str, Any] = None) -> str:
    cfg = cfg or config_mod.load()
    return os.path.join(config_mod.path_of("data", cfg), "completion.json")


def load(cfg: Dict[str, Any] = None) -> Dict[str, Any]:
    data = config_mod.read_json(data_path(cfg))     # utf-8-sig：容忍记事本写入的 BOM
    if not data:
        return {"items": [], "kinds": dict(DEFAULT_KINDS)}
    data.setdefault("kinds", dict(DEFAULT_KINDS))
    return data


def items(cfg: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    return list(load(cfg).get("items") or [])


def stats(cfg: Dict[str, Any] = None) -> Dict[str, int]:
    its = items(cfg)
    by_kind: Dict[str, int] = {}
    for it in its:
        by_kind[it.get("kind", "?")] = by_kind.get(it.get("kind", "?"), 0) + 1
    return {"total": len(its), **by_kind}


def build_script(cfg: Dict[str, Any] = None) -> str:
    """生成注入用的 <script type="module">。"""
    data = load(cfg)
    its = data.get("items") or []
    kinds = dict(DEFAULT_KINDS)
    kinds.update(data.get("kinds") or {})
    # 嵌入 JSON 时把 </ 转义，避免数据里出现 </script> 提前结束脚本
    payload = json.dumps({"items": its, "kinds": kinds},
                         ensure_ascii=False).replace("</", "<\\/")

    return """
<script type="module">
window.__ctutor_comp = {stage: "start"};
(async () => {
  try {
    const mod = await import('./assets/monaco-core.js');
    const monaco = mod.r || mod.m || mod;
    // 暴露句柄供 Python 侧调试/自检使用。
    // 注意：runJavaScript 的执行上下文 base URL 是 about:blank，那里无法 import()，
    // 因此必须由页面内的脚本把句柄挂出来，外部才能拿到 monaco。
    window.__ctutor_monaco = monaco;
    const DATA = %s;
    const K = DATA.kinds;

    monaco.languages.registerCompletionItemProvider('c', {
      triggerCharacters: ['.', '>', '#'],
      provideCompletionItems: function (model, position) {
        const w = model.getWordUntilPosition(position);
        const range = {
          startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
          startColumn: w.startColumn, endColumn: w.endColumn
        };
        const out = DATA.items.map(function (it) {
          const s = {
            label: it.label,
            kind: (K[it.kind] !== undefined ? K[it.kind] : 1),
            detail: it.detail || '',
            documentation: {value: it.doc || ''},
            range: range,
            sortText: it.sort || ('5_' + it.label)
          };
          if (it.insert) {
            s.insertText = it.insert;
            s.insertTextRules = 4;          // InsertAsSnippet
          } else {
            s.insertText = it.label;
          }
          return s;
        });
        return {suggestions: out};
      }
    });

    // 输入字母就弹建议；fixedOverflowWidgets 防止建议框被 WebEngine 视口裁掉
    const eds = (monaco.editor && monaco.editor.getEditors) ? monaco.editor.getEditors() : [];
    eds.forEach(function (ed) {
      ed.updateOptions({
        quickSuggestions: {other: true, comments: false, strings: false},
        suggestOnTriggerCharacters: true,
        tabCompletion: 'on',
        acceptSuggestionOnEnter: 'on',
        fixedOverflowWidgets: true,
        snippetSuggestions: 'top',
        suggest: {showWords: true, filterGraceful: true}
      });
    });

    window.__ctutor_comp = {stage: "done", registered: true,
                            count: DATA.items.length, editors: eds.length};
  } catch (e) {
    window.__ctutor_comp = {stage: "error",
                            error: String(e && e.message ? e.message : e)};
  }
})();
</script>
""" % payload
