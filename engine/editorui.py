# -*- coding: utf-8 -*-
"""编辑器内的错误标记（Monaco 装饰器）。

需求：把出错的地方在编辑器里标红，而不是只在下方讲解里给个行号。

设计：
  · **选中的那一条**用强红：整行淡红底 + 左侧红边 + 行号区红点 + 具体 token 加下划线；
  · **其它错误**用浅红：只做整行淡红 + 行号区橙点，让学生知道"还有别处也错了"，
    但不抢走注意力（文献结论：一次只讲一条，避免信息过载）；
  · 警告默认不标，否则一片花花绿绿反而看不清重点。

注入方式与补全一致：覆盖 Monaco._load_editor()，不改第三方包。
两段脚本各自 import 同一份 monaco-core.js —— ES 模块有缓存，拿到的是同一个实例。
"""
from typing import Any, Dict, List

CSS = """
<style>
/* 选中的错误：整行淡红底 + 左侧红边 */
.ctutor-err-primary-line {
  background: rgba(220, 38, 38, 0.16);
  border-left: 3px solid #dc2626;
}
/* 其它错误：只给很淡的底 */
.ctutor-err-line {
  background: rgba(220, 38, 38, 0.07);
}
/* 具体出错的 token 加红色下划线 */
.ctutor-err-primary-token {
  background: rgba(220, 38, 38, 0.32);
  border-bottom: 2px solid #dc2626;
}
.ctutor-err-token {
  border-bottom: 2px solid rgba(220, 38, 38, 0.65);
}
/* 行号区的小圆点：滚动到别处也能一眼看到哪几行有问题 */
.ctutor-err-primary-glyph::before {
  content: '';
  display: block;
  width: 9px; height: 9px;
  margin: 5px 0 0 4px;
  border-radius: 50%;
  background: #dc2626;
}
.ctutor-err-glyph::before {
  content: '';
  display: block;
  width: 7px; height: 7px;
  margin: 6px 0 0 5px;
  border-radius: 50%;
  background: #f59e0b;
}
/* 滚动条右侧的标记 */
.ctutor-err-ruler { background: #dc2626; width: 3px !important; }
</style>
"""

SCRIPT = r"""
<script type="module">
window.__ctutor_mark_state = {stage: 'start'};
(async () => {
  try {
    const mod = await import('./assets/monaco-core.js');
    const monaco = mod.r || mod.m || mod;
    window.__ctutor_monaco = monaco;

    function editors() {
      return (monaco.editor && monaco.editor.getEditors) ? monaco.editor.getEditors() : [];
    }

    // 打开行号区渲染（默认可能没开），否则行号边上的圆点画不出来
    function ensureOptions(ed) {
      try {
        ed.updateOptions({
          glyphMargin: true,
          lineDecorationsWidth: 10,
          fixedOverflowWidgets: true,
          // Ctrl+滚轮缩放字号：Monaco 自带的能力，比自己实现一套滚轮监听可靠
          mouseWheelZoom: true
        });
      } catch (e) { /* 忽略 */ }
    }

    // ---- 字号缩放 ----
    const BASE_FONT = 14;
    window.__ctutor_font_size = BASE_FONT;
    window.__ctutor_zoom = function (step) {
      const eds = editors();
      if (!eds.length) return 'no-editor';
      const size = Math.max(8, Math.min(40, BASE_FONT + (step | 0) * 2));
      try {
        eds[0].updateOptions({ fontSize: size });
        window.__ctutor_font_size = size;
        return 'ok:' + size;
      } catch (e) { return 'error:' + e.message; }
    };

    function apply(ed, decos) {
      if (ed.createDecorationsCollection) {
        if (!ed.__ctutorDeco) {
          ed.__ctutorDeco = ed.createDecorationsCollection(decos);
        } else {
          ed.__ctutorDeco.set(decos);
        }
        const r = ed.__ctutorDeco.getRanges ? ed.__ctutorDeco.getRanges() : decos;
        return r ? r.length : decos.length;
      }
      // 老版本 Monaco 的退路
      if (!ed.__ctutorOldIds) ed.__ctutorOldIds = [];
      ed.__ctutorOldIds = ed.deltaDecorations(ed.__ctutorOldIds, decos);
      return ed.__ctutorOldIds.length;
    }

    // items: [{line, col, message, level, primary}]
    window.__ctutor_mark = function (items) {
      const eds = editors();
      if (!eds.length) { window.__ctutor_mark_state = {stage: 'no-editor'}; return 'no-editor'; }
      const ed = eds[0];
      ensureOptions(ed);
      const model = ed.getModel();
      const decos = [];

      (items || []).forEach(function (it) {
        const line = Math.max(1, it.line | 0);
        if (!line) return;
        const col = Math.max(1, it.col | 0);
        const maxCol = model ? model.getLineMaxColumn(line) : col + 1;

        // 整行标记
        decos.push({
          range: new monaco.Range(line, 1, line, maxCol),
          options: {
            isWholeLine: true,
            className: it.primary ? 'ctutor-err-primary-line' : 'ctutor-err-line',
            glyphMarginClassName: it.primary ? 'ctutor-err-primary-glyph' : 'ctutor-err-glyph',
            overviewRuler: {
              color: it.primary ? '#dc2626' : '#f59e0b',
              position: monaco.editor.OverviewRulerLane.Right
            },
            hoverMessage: { value: (it.message || '').replace(/[\\r\\n]+/g, ' ').slice(0, 300) }
          }
        });

        // 具体 token 加下划线：优先取编辑器认可的"单词"，取不到就标一个字符
        let endCol = col + 1;
        if (model) {
          const w = model.getWordAtPosition({ lineNumber: line, column: col });
          if (w && w.endColumn) endCol = w.endColumn;
          else endCol = Math.min(col + 1, maxCol);
        }
        decos.push({
          range: new monaco.Range(line, col, line, Math.max(col + 1, endCol)),
          options: {
            inlineClassName: it.primary ? 'ctutor-err-primary-token' : 'ctutor-err-token'
          }
        });
      });

      const n = apply(ed, decos);
      window.__ctutor_mark_state = { stage: 'done', count: n, lines: (items || []).length };
      return 'ok:' + n;
    };

    window.__ctutor_clear_marks = function () {
      const eds = editors();
      if (!eds.length) return 'no-editor';
      apply(eds[0], []);
      window.__ctutor_mark_state = { stage: 'done', count: 0, lines: 0 };
      return 'cleared';
    };

    // 滚动到主错误那一行（不抢焦点，避免打断打字）
    window.__ctutor_reveal = function (line) {
      const eds = editors();
      if (!eds.length) return 'no-editor';
      try {
        eds[0].revealLineInCenterIfOutsideViewport(Math.max(1, line | 0));
        return 'ok';
      } catch (e) { return 'error:' + e.message; }
    };

    window.__ctutor_mark_state = { stage: 'ready' };
  } catch (e) {
    window.__ctutor_mark_state = { stage: 'error', error: String(e && e.message ? e.message : e) };
  }
})();

// ---------------------------------------------------------------- 右键菜单汉化
// Monaco 没有给中文语境提供现成的菜单文案，右键出来全是 Cut / Copy / Paste。
// 不去改第三方包（下次升级就被覆盖），而是**监听 DOM**：菜单一出现就替换文字。
// 这样做的好处是不会破坏任何命令绑定——只改显示，行为完全不变。
(function () {
  const MAP = {
    'Cut': '剪切',
    'Copy': '复制',
    'Paste': '粘贴',
    'Copy As': '复制为',
    'Copy with Syntax Highlighting': '复制（保留语法高亮）',
    'Select All': '全选',
    'Command Palette': '命令面板',
    'Go to Symbol...': '跳到符号…',
    'Go to Symbol': '跳到符号',
    'Go to Definition': '跳到定义',
    'Go to Type Definition': '跳到类型定义',
    'Go to Declaration': '跳到声明',
    'Go to Implementations': '跳到实现',
    'Go to References': '查看引用',
    'Peek Definition': '预览定义',
    'Peek Declaration': '预览声明',
    'Peek Type Definition': '预览类型定义',
    'Peek Implementations': '预览实现',
    'Peek References': '预览引用',
    'Rename Symbol': '重命名符号',
    'Change All Occurrences': '修改所有匹配项',
    'Format Document': '格式化文档',
    'Format Selection': '格式化选中部分',
    'Toggle Line Comment': '切换行注释',
    'Toggle Block Comment': '切换块注释',
    'Undo': '撤销',
    'Redo': '重做',
    'Delete': '删除',
    'Show Whitespace': '显示空白字符',
    'Show Minimap': '显示缩略图',
    'Linked Editing': '联动编辑',
    'Trigger Parameter Hints': '显示参数提示',
    'Trigger Suggest': '触发补全',
    'Source Action...': '源码操作…',
    'Refactor...': '重构…',
  };

  function translate(root) {
    if (!root || !root.querySelectorAll) return 0;
    let n = 0;
    root.querySelectorAll('.action-label').forEach(function (el) {
      const raw = (el.textContent || '').trim();
      const zh = MAP[raw];
      if (zh && raw !== zh) { el.textContent = zh; n++; }
    });
    return n;
  }

  // 菜单是懒创建的，出现时机不确定，所以用 MutationObserver 盯着
  const obs = new MutationObserver(function (muts) {
    for (const m of muts) {
      const nodes = m.addedNodes || [];
      for (const node of nodes) {
        if (node.nodeType !== 1) continue;
        const looksMenu = (node.classList && node.classList.contains('monaco-menu'))
          || (node.querySelector && node.querySelector('.monaco-menu'));
        if (!looksMenu) continue;
        // 刚插入的节点可能还没填完文字，等一帧再翻
        setTimeout(function () { translate(node); }, 0);
        setTimeout(function () { translate(node); }, 60);
      }
    }
  });
  try {
    obs.observe(document.body, { childList: true, subtree: true });
    window.__ctutor_ctxmenu_zh = true;
  } catch (e) {
    window.__ctutor_ctxmenu_zh = false;
  }
})();
</script>
"""


def build_script() -> str:
    """返回注入用的 CSS + <script>。"""
    return CSS + SCRIPT


def items_from_diagnostics(diagnostics: List[Any], chosen: Any = None,
                           include_warnings: bool = False) -> List[Dict[str, Any]]:
    """把诊断列表转成注入脚本要的标记项。

    规则：错误全部标记；警告默认不标（避免页面花掉、抢走注意力）。
    选中的那一条标 primary=True —— 它是讲解对象。
    """
    out: List[Dict[str, Any]] = []
    chosen_key = None
    if chosen is not None:
        chosen_key = (getattr(chosen, "line", 0), getattr(chosen, "column", 0),
                      getattr(chosen, "code", ""), getattr(chosen, "message", ""))
    for d in diagnostics:
        if not getattr(d, "line", 0):
            continue
        key = (d.line, d.column, d.code, d.message)
        is_chosen = chosen_key is not None and key == chosen_key
        # 选中的那一条**永远要标**，不管它是错误还是警告。
        # 否则会出现这种别扭情况：被讲解的是一句警告，页面上另有错误，
        # 结果别的行标红了，正在讲的那一行反而没标 —— 高亮和讲解对不上。
        if not (is_chosen or d.is_error or (include_warnings and d.is_warning)):
            continue
        out.append({
            "line": d.line,
            "col": d.column or 1,
            "level": d.level,
            "message": d.message,
            "primary": is_chosen,
        })
    return out
