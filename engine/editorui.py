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

import json

# ---------------------------------------------------------------- 右键菜单内容
# 为什么自己画菜单，而不是去改 Monaco 的菜单：
#   这个 qtmonaco 带的 monaco-core.js **没有右键菜单那部分贡献**（派发 contextmenu
#   之后 DOM 里始终没有 .monaco-menu），所以右键时页面不出菜单，浏览器就把请求交给
#   Qt WebEngine，弹出它自己的**原生菜单**——那是 C++ 画的，网页改不了文字，
#   Qt 那边也只能整块替换。
# 于是干脆自己接管：页面里 preventDefault 掉原生菜单，画一个全中文的。
# 菜单项 = (中文名, 动作 id, 快捷键提示)。'-' 表示分隔线。
MENU_ITEMS = [
    ("剪切", "ctutor_cut", "Ctrl+X"),
    ("复制", "ctutor_copy", "Ctrl+C"),
    ("粘贴", "ctutor_paste", "Ctrl+V"),
    ("删除", "ctutor_delete", "Del"),
    "-",
    ("全选", "ctutor_selectall", "Ctrl+A"),
    "-",
    ("添加锚点…", "ctutor_anchor_add", ""),
    ("锚点列表…", "ctutor_anchor_list", ""),
    ("删除本行锚点", "ctutor_anchor_del", ""),
    "-",
    ("撤销", "ctutor_undo", "Ctrl+Z"),
    ("重做", "ctutor_redo", "Ctrl+Y"),
]

# ---------------------------------------------------------------- 右键菜单汉化表
# 放这儿而不是只写在 JS 里：网页里的菜单用它翻译，Qt 原生弹菜单也用同一张表，
# 不会出现"网页是中文、原生菜单还是英文"这种半吊子状态。
MENU_ZH = {
    # 剪贴板
    "Cut": "剪切",
    "Copy": "复制",
    "Paste": "粘贴",
    "Copy As": "复制为",
    "Copy with Syntax Highlighting": "复制（保留语法高亮）",
    "Select All": "全选",
    "Command Palette": "命令面板",
    # 跳转
    "Go to Symbol...": "跳到符号…",
    "Go to Symbol": "跳到符号",
    "Go to Definition": "跳到定义",
    "Go to Type Definition": "跳到类型定义",
    "Go to Declaration": "跳到声明",
    "Go to Implementations": "跳到实现",
    "Go to References": "查看引用",
    "Go to Bracket": "跳到配对括号",
    # 预览
    "Peek Definition": "预览定义",
    "Peek Declaration": "预览声明",
    "Peek Type Definition": "预览类型定义",
    "Peek Implementations": "预览实现",
    "Peek References": "预览引用",
    # 编辑
    "Rename Symbol": "重命名符号",
    "Change All Occurrences": "修改所有匹配项",
    "Format Document": "格式化文档",
    "Format Selection": "格式化选中部分",
    "Toggle Line Comment": "切换行注释",
    "Toggle Block Comment": "切换块注释",
    "Undo": "撤销",
    "Redo": "重做",
    "Delete": "删除",
    "Delete Line": "删除整行",
    "Duplicate Selection": "复制选中内容",
    "Copy Line Up": "向上复制一行",
    "Copy Line Down": "向下复制一行",
    "Move Line Up": "上移一行",
    "Move Line Down": "下移一行",
    # 显示
    "Show Whitespace": "显示空白字符",
    "Show Minimap": "显示缩略图",
    "Toggle Minimap": "显示/隐藏缩略图",
    # 补全与重构
    "Linked Editing": "联动编辑",
    "Trigger Parameter Hints": "显示参数提示",
    "Trigger Suggest": "触发补全",
    "Source Action...": "源码操作…",
    "Refactor...": "重构…",
    # 兜底（不同版本的写法）
    "Select All Occurrences": "选中所有匹配项",
    "Add Line Comment": "添加行注释",
}

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
/* 锚点：贴在编辑器**最右端**的小标签，点一下就跳过去。
   为什么不画在行首：行号区太窄、和错误红点挤在一起，学生看不出来；
   放右边既不挡代码，也能直接读到锚点名字。 */
.ctutor-anchor-layer {
  position: absolute;
  top: 0; left: 0;
  /* 尺寸不写死：Monaco 的 overlayWidgets 容器高度是 0，写 inset:0 会被裁成 0 高，
     标签虽然算出了坐标却一个都看不见。改由 JS 按编辑器实际尺寸设置。 */
  width: 0; height: 0;
  pointer-events: none;        /* 整层不挡鼠标，只让标签本身可点 */
  z-index: 25;
  overflow: hidden;
}
.ctutor-anchor-flag {
  position: absolute;
  pointer-events: auto;
  cursor: pointer;
  max-width: 132px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  line-height: 16px;
  height: 16px;
  padding: 0 7px;
  border-radius: 8px 0 0 8px;
  background: rgba(37, 99, 235, 0.92);
  color: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.28);
  user-select: none;
}
.ctutor-anchor-flag:hover { background: #1d4ed8; }

/* 自己画的右键菜单（全中文）。原生菜单是 C++ 画的，改不了文字，只能自己接管。 */
.ctutor-menu {
  position: fixed;
  z-index: 99999;
  min-width: 196px;
  padding: 5px 0;
  background: #252526;
  color: #e6e6e6;
  border: 1px solid #464646;
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  font: 13px/1.45 "Microsoft YaHei", "Segoe UI", sans-serif;
  user-select: none;
}
.ctutor-menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  padding: 5px 14px;
  cursor: default;
  white-space: nowrap;
}
.ctutor-menu-item:hover { background: #094771; }
.ctutor-menu-key {
  color: #a0a0a0;
  font-size: 12px;
}
.ctutor-menu-sep {
  height: 1px;
  margin: 4px 0;
  background: #464646;
}
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

// ---------------------------------------------------------------- 锚点（书签）
// 只在编辑器里画标记，**不改一行代码**：装饰器是纯显示层，文件内容一个字节都不动，
// 保存 / 对照 / AI 都看不到它们。行号会随代码增删自动跟着走（Monaco 的装饰器
// 跟着内容走，不是死死绑定数字）。
(function () {
  function eds() {
    const m = window.__ctutor_monaco;
    return (m && m.editor && m.editor.getEditors) ? m.editor.getEditors() : [];
  }

  // 行号左边那条窄带（glyph margin）默认是关的。不打开它，
  // 装饰器对象照样能创建、getRanges() 也有值，但**屏幕上什么都看不见**——
  // 错误的红色小圆点就是这个坑（只能靠数 DOM 节点发现）。
  // 锚点已经不靠这条窄带了（改成贴右边的标签），但这里仍要打开，
  // 否则错误标记又变成看不见。
  // 注意：这里只管 glyphMargin 一项，别的选项由标红那一套负责，
  // 别跨作用域去调那边的函数（会 ReferenceError，把整个锚点功能带崩）。
  function ensureGlyphMargin(ed) {
    try {
      const m = window.__ctutor_monaco;
      if (ed.getOption(m.editor.EditorOption.glyphMargin)) return true;
      ed.updateOptions({ glyphMargin: true });
      return !!ed.getOption(m.editor.EditorOption.glyphMargin);
    } catch (e) { return false; }
  }

  // ---- 锚点：贴在编辑器**最右端**的小标签 ----
  // 为什么不用装饰器直接画：装饰器只能往行号区、行尾窄带里塞东西，
  // 塞出来的东西要么在左边（和错误红点打架），要么宽度算不准。
  // 所以改成"覆盖整个编辑器的透明图层 + 自己按行算位置"：
  //   · 标签固定贴右边，不挡代码；
  //   · 位置每帧从装饰器读当前行号，所以编辑内容后锚点会跟着行走；
  //   · 点标签直接跳过去。
  const anchorState = { items: [], flags: [], layer: null, hooks: false };

  function anchorLayerOf(ed) {
    if (anchorState.layer && document.body.contains(anchorState.layer)) {
      return anchorState.layer;
    }
    const layer = document.createElement('div');
    layer.className = 'ctutor-anchor-layer';
    try {
      ed.addOverlayWidget({
        getDomNode: function () { return layer; },
        getId: function () { return 'ctutor.anchor.layer'; },
        getPosition: function () { return null; }     // 位置由我们自己算
      });
    } catch (e) { /* 忽略：极端情况下标签层建不出来，装饰器还在 */ }
    anchorState.layer = layer;
    return layer;
  }

  function anchorRanges(ed) {
    return (ed.__ctutorAnchorDeco && ed.__ctutorAnchorDeco.getRanges)
      ? ed.__ctutorAnchorDeco.getRanges() : [];
  }

  // 第 i 个锚点现在在第几行：优先听装饰器的（编辑后会自动移动）
  function anchorLineOf(ed, i) {
    const r = anchorRanges(ed)[i];
    if (r) return r.startLineNumber;
    const it = anchorState.items[i];
    return it ? Math.max(1, it.line | 0) : 1;
  }

  // Monaco 的 .overlayWidgets 容器自身高度是 0（它只做定位，不占空间），
  // 所以这一层必须自己量出编辑器的宽高，否则子节点全被裁掉——表现就是
  // "坐标都对、DOM 都在，屏幕上什么都没有"。
  function anchorSizeLayer(ed) {
    const layer = anchorState.layer;
    if (!layer) return;
    let w = 0, h = 0;
    try {
      const li = ed.getLayoutInfo ? ed.getLayoutInfo() : null;
      if (li) { w = li.width; h = li.height; }
    } catch (e) { /* 忽略 */ }
    if (!w || !h) {
      const dom = ed.getDomNode ? ed.getDomNode() : null;
      if (dom) { w = dom.clientWidth; h = dom.clientHeight; }
    }
    if (w > 0) layer.style.width = w + 'px';
    if (h > 0) layer.style.height = h + 'px';
  }

  function anchorLayout() {
    const list = eds();
    if (!list.length) return 'no-editor';
    const ed = list[0];
    anchorSizeLayer(ed);
    const li = ed.getLayoutInfo ? ed.getLayoutInfo() : null;
    const mini = li && li.minimap ? (li.minimap.minimapWidth || 0) : 0;
    const sb = li ? (li.verticalScrollbarWidth || 0) : 0;
    const h = li ? li.height : 0;
    const right = mini + sb + 6;      // 缩略图/滚动条左边一点点
    anchorState.flags.forEach(function (f, i) {
      const line = anchorLineOf(ed, i);
      let pos = null;
      try {
        pos = ed.getScrolledVisiblePosition({ lineNumber: line, column: 1 });
      } catch (e) { pos = null; }
      if (!pos || pos.top < -24 || (h && pos.top > h)) {
        f.el.style.display = 'none';        // 滚出视野就先藏起来
        return;
      }
      f.el.style.display = '';
      f.el.style.right = right + 'px';
      f.el.style.top = Math.round(pos.top) + 'px';
    });
    return 'ok';
  }

  function anchorRender() {
    const list = eds();
    if (!list.length) return 'no-editor';
    const ed = list[0];
    const layer = anchorLayerOf(ed);
    layer.innerHTML = '';
    anchorState.flags = [];
    anchorState.items.forEach(function (it, i) {
      const el = document.createElement('div');
      el.className = 'ctutor-anchor-flag';
      el.textContent = '⚑ ' + (it.name || ('第 ' + (it.line | 0) + ' 行'));
      el.title = '锚点：' + (it.name || '') + '（点一下跳过去）';
      el.addEventListener('mousedown', function (e) { e.preventDefault(); });
      el.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        const line = anchorLineOf(ed, i);
        try {
          ed.setPosition({ lineNumber: line, column: 1 });
          ed.revealLineInCenter(line);
          ed.focus();
        } catch (err) { /* 忽略 */ }
      });
      layer.appendChild(el);
      anchorState.flags.push({ el: el });
    });
    anchorLayout();
    if (!anchorState.hooks) {
      anchorState.hooks = true;
      const relayout = function () { anchorLayout(); };
      try {
        ed.onDidScrollChange(relayout);
        ed.onDidLayoutChange(relayout);
        ed.onDidChangeModelContent(relayout);
      } catch (e) { /* 忽略 */ }
      window.addEventListener('resize', relayout);
    }
    return 'ok';
  }

  // items: [{name, line, text}]
  window.__ctutor_set_anchors = function (items) {
    const list = eds();
    if (!list.length) return 'no-editor';
    const ed = list[0], m = window.__ctutor_monaco;
    ensureGlyphMargin(ed);
    const decos = (items || []).map(function (it) {
      const line = Math.max(1, it.line | 0);
      return {
        range: new m.Range(line, 1, line, 1),
        options: {
          // 本身不可见，只起两个作用：跟着编辑移动、在右侧滚动条上留个蓝条
          overviewRuler: { color: '#2563eb', position: m.editor.OverviewRulerLane.Right },
          hoverMessage: { value: '锚点：' + (it.name || '') }
        }
      };
    });
    // 单独一个 decorations collection：不能复用标红那一个，否则会互相冲掉
    if (ed.createDecorationsCollection) {
      if (!ed.__ctutorAnchorDeco) {
        ed.__ctutorAnchorDeco = ed.createDecorationsCollection(decos);
      } else {
        ed.__ctutorAnchorDeco.set(decos);
      }
    } else {
      ed.__ctutorAnchorIds = ed.deltaDecorations(ed.__ctutorAnchorIds || [], decos);
    }
    anchorState.items = items || [];
    window.__ctutor_anchors = anchorState.items;
    anchorRender();
    return 'ok:' + decos.length;
  };

  // 给外部（测试/面板）手动触发重排用
  window.__ctutor_anchor_layout = anchorLayout;

  window.__ctutor_goto_anchor = function (line) {
    const list = eds();
    if (!list.length) return 'no-editor';
    const ed = list[0], n = Math.max(1, line | 0);
    try {
      ed.setPosition({ lineNumber: n, column: 1 });
      ed.revealLineInCenter(n);
      ed.focus();
      return 'ok';
    } catch (e) { return 'error:' + e.message; }
  };

  // 当前光标行 + 该行原文：右键「添加锚点」时用来起默认名字
  window.__ctutor_cursor_info = function () {
    const list = eds();
    if (!list.length) return '';
    const ed = list[0], p = ed.getPosition(), model = ed.getModel();
    const line = p ? p.lineNumber : 1;
    return JSON.stringify({ line: line, text: model ? model.getLineContent(line) : '' });
  };

  // 编辑器一就绪就把行号区打开。以前这个选项只在"第一次标红"时才设，
  // 结果是不检查的时候，锚点（和错误行号圆点）都看不见。
  (function bootGlyphMargin() {
    let tries = 0;
    const t = setInterval(function () {
      const list = eds();
      if (list.length) {
        if (ensureGlyphMargin(list[0])) clearInterval(t);
        return;
      }
      if (++tries > 50) clearInterval(t);      // 最多等 10 秒
    }, 200);
  })();
})();

// ---------------------------------------------------------------- 右键菜单汉化
// Monaco 没有中文菜单文案，右键出来全是 Cut / Copy / Paste。
// 不去改第三方包（下次升级就被覆盖），而是**只改显示文字**，命令绑定完全不动。
//
// 踩过的坑，都写在这儿免得再犯：
//  1) 菜单容器 .context-view/.monaco-menu **只创建一次**，之后每次右键只是把里面的
//     条目换掉。只监听"新增菜单节点"的话，只有第一次能翻到。
//  2) 条目可能在翻译之后又被 Monaco 重新渲染回英文（选中文字后条目变多就是这样）。
//  3) 所以最终方案是三保险：MutationObserver 盯内部变化 + 右键瞬间连翻 4 次 +
//     每 250ms 轮询一次。轮询看着笨，但它不依赖"菜单挂在哪、什么时候渲染"。
(function () {
  const MAP = __MENU_ZH__;

  // 把一个 .action-label 换成中文。返回 1 表示这一轮真的改了。
  // 两种真实结构都要管：
  //   1) <a class="action-label">Cut</a>
  //   2) <a class="action-label">Cut<span class="keybinding">Ctrl+X</span></a>
  // 第 2 种不能整块 textContent 覆盖，否则会把快捷键提示抹掉。
  function translateEl(el) {
    const raw = (el.textContent || '').trim();
    if (!raw) return 0;
    const zh = MAP[raw];
    if (zh) {
      if (raw === zh) return 0;
      if (!el.children || !el.children.length) { el.textContent = zh; return 1; }
      const f = el.firstChild;
      if (f && f.nodeType === 3 && f.nodeValue.indexOf(raw) >= 0) {
        f.nodeValue = f.nodeValue.split(raw).join(zh);
        return 1;
      }
      return 0;
    }
    // 文字和快捷键被塞进同一个文本节点的情况（"Cut Ctrl+X"）
    const f2 = el.firstChild;
    if (f2 && f2.nodeType === 3) {
      const ft = (f2.nodeValue || '').trim();
      if (MAP[ft]) { f2.nodeValue = f2.nodeValue.replace(ft, MAP[ft]); return 1; }
    }
    return 0;
  }

  function translateAll() {
    let n = 0;
    const els = document.querySelectorAll('.action-label');
    for (let i = 0; i < els.length; i++) n += translateEl(els[i]);
    return n;
  }

  // 第二层保险：**不认类名**，直接按文字节点匹配。
  // 万一菜单结构的类名不是 .action-label（换个 Monaco 版本就可能变），
  // 只要那个文字节点本身就是 "Cut"/"Copy" 这种整词，就换掉它。
  function translateTextNodes(root) {
    if (!root || !document.createTreeWalker) return 0;
    let n = 0;
    try {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
      let node = walker.nextNode();
      while (node) {
        const raw = (node.nodeValue || '').trim();
        const zh = MAP[raw];
        // 只动"这个文字节点自成一个标签"的情况：父元素里没有别的元素子节点，
        // 否则可能把整块内容覆盖掉（比如父容器里还有快捷键 span）。
        if (zh && raw !== zh && node.parentNode && !node.parentNode.children.length) {
          node.nodeValue = node.nodeValue.replace(raw, zh);
          n++;
        }
        node = walker.nextNode();
      }
    } catch (e) { /* 忽略 */ }
    return n;
  }

  // 页面上有没有菜单（含"容器复用但内容换了"的情况）。
  // 注意不能只认"可见"：Monaco 的容器平时是 display:none，条目刚渲染出来时
  // 尺寸可能还是 0，用可见性当条件会把刚出现的菜单漏掉。
  function hasMenu() {
    if (document.querySelector('.monaco-menu')) return true;
    const views = document.querySelectorAll('.context-view');
    for (let i = 0; i < views.length; i++) {
      if (views[i].children.length) return true;
    }
    return false;
  }

  // 第一次看到真菜单时，把它的 HTML 留一份。万一还有没翻到的条目，
  // 面板那边会把它写进临时目录——用来核对真实结构，而不是靠猜。
  function capture() {
    if (window.__ctutor_menu_dump) return;
    const m = document.querySelector('.monaco-menu');
    if (!m) return;
    const host = (m.closest && m.closest('.context-view')) || m;
    window.__ctutor_menu_dump = host.outerHTML.slice(0, 20000);
    window.__ctutor_menu_labels = Array.prototype.map.call(
      m.querySelectorAll('.action-label'), function (e) {
        return (e.textContent || '').trim();
      });
  }

  // 把所有菜单里的条目翻一遍：先按类名，再按文字节点（兜住结构变化）
  function translateMenus() {
    let n = translateAll();
    const menus = document.querySelectorAll('.monaco-menu');
    for (let i = 0; i < menus.length; i++) n += translateTextNodes(menus[i]);
    const views = document.querySelectorAll('.context-view');
    for (let i = 0; i < views.length; i++) {
      if (views[i].children.length) n += translateTextNodes(views[i]);
    }
    return n;
  }

  let queued = false;
  function flush() {
    queued = false;
    capture();
    return translateMenus();
  }
  function schedule() {
    if (queued) return;
    queued = true;
    if (window.requestAnimationFrame) requestAnimationFrame(flush);
    else setTimeout(flush, 16);
  }

  try {
    new MutationObserver(function (muts) {
      for (let i = 0; i < muts.length; i++) {
        const m = muts[i];
        if (m.type === 'characterData') {
          const p = m.target.parentNode;
          if (p && p.closest && p.closest('.monaco-menu')) { schedule(); return; }
          continue;
        }
        const t = m.target;
        if (t && t.closest && t.closest('.monaco-menu')) { schedule(); return; }
        const added = m.addedNodes || [];
        for (let j = 0; j < added.length; j++) {
          const n = added[j];
          if (n.nodeType !== 1) continue;
          const cls = n.classList || { contains: function () { return false; } };
          if (cls.contains('monaco-menu') || cls.contains('context-view')
              || (n.querySelector && n.querySelector('.monaco-menu'))) {
            schedule();
            return;
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  } catch (e) { /* 忽略：轮询还能兜底 */ }

  // 右键/菜单键按下的瞬间连翻几次，尽量让人眼看不到那一帧英文
  ['contextmenu', 'mousedown', 'mouseup', 'keydown'].forEach(function (evt) {
    document.addEventListener(evt, function () {
      setTimeout(flush, 0);
      setTimeout(flush, 40);
      setTimeout(flush, 120);
      setTimeout(flush, 300);
    }, true);
  });

  // 兜底轮询：菜单什么时候渲染、渲染几次、挂在哪，都不用关心。
  setInterval(function () {
    if (!hasMenu()) return;
    capture();
    translateMenus();
  }, 250);

  window.__ctutor_ctxmenu_zh = true;
})();

// ---------------------------------------------------------------- 右键菜单（自己画）
// 这个 monaco-core 里没有右键菜单贡献：派发 contextmenu 之后 DOM 里根本不出现
// .monaco-menu，于是浏览器把请求交给 Qt，弹出的是一张 C++ 原生菜单（英文）。
// 原生菜单没法从网页改文字，所以这里 preventDefault 掉它，自己画一张中文的。
(function () {
  const ITEMS = __MENU_ITEMS__;
  let menuEl = null;

  function closeMenu() {
    if (menuEl && menuEl.parentNode) menuEl.parentNode.removeChild(menuEl);
    menuEl = null;
    window.__ctutor_menu_open = false;
  }

  function editorUnder(node) {
    const m = window.__ctutor_monaco;
    if (!m || !m.editor || !m.editor.getEditors) return null;
    const list = m.editor.getEditors();
    for (let i = 0; i < list.length; i++) {
      const dom = list[i].getDomNode ? list[i].getDomNode() : null;
      if (dom && node && dom.contains(node)) return list[i];
    }
    return null;
  }

  // 执行一条菜单命令。撤销/重做/全选直接走编辑器核心命令，
  // 其余（剪切/复制/粘贴/删除/锚点）交给面板那边处理。
  function runAction(ed, id) {
    try {
      if (id === 'ctutor_undo') { ed.focus(); ed.trigger('keyboard', 'undo'); return true; }
      if (id === 'ctutor_redo') { ed.focus(); ed.trigger('keyboard', 'redo'); return true; }
      const acts = ed.getSupportedActions ? ed.getSupportedActions() : [];
      for (let i = 0; i < acts.length; i++) {
        const a = acts[i];
        if (a.id === id || (a.id.length > id.length && a.id.slice(-id.length) === id)) {
          // 注意：不要用 isSupported() 过滤。我们注册的命令带了"语言必须是 C"的前置
          // 条件，在别的语言里会被判成不支持，菜单就静悄悄没反应了。
          a.run();
          return true;
        }
      }
      // 兜底：还没注册成编辑器命令时，退化成触发同名命令
      ed.trigger('keyboard', id);
      return true;
    } catch (e) {
      return false;
    }
  }

  function openMenu(ed, x, y) {
    closeMenu();
    const box = document.createElement('div');
    box.className = 'ctutor-menu';
    for (let i = 0; i < ITEMS.length; i++) {
      const it = ITEMS[i];
      if (!it || it === '-' || !it[1]) {
        const sep = document.createElement('div');
        sep.className = 'ctutor-menu-sep';
        box.appendChild(sep);
        continue;
      }
      const row = document.createElement('div');
      row.className = 'ctutor-menu-item';
      row.setAttribute('data-id', it[1]);
      const label = document.createElement('span');
      label.className = 'ctutor-menu-label';
      label.textContent = it[0];
      row.appendChild(label);
      if (it[2]) {
        const key = document.createElement('span');
        key.className = 'ctutor-menu-key';
        key.textContent = it[2];
        row.appendChild(key);
      }
      row.addEventListener('mousedown', function (e) {
        e.preventDefault();
        e.stopPropagation();
      });
      row.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        const id = it[1];
        closeMenu();
        runAction(ed, id);
      });
      box.appendChild(row);
    }
    document.body.appendChild(box);
    const w = box.offsetWidth || 200;
    const h = box.offsetHeight || 300;
    box.style.left = Math.max(4, Math.min(x, window.innerWidth - w - 6)) + 'px';
    box.style.top = Math.max(4, Math.min(y, window.innerHeight - h - 6)) + 'px';
    menuEl = box;
    window.__ctutor_menu_open = true;
    return box;
  }

  document.addEventListener('contextmenu', function (e) {
    const ed = editorUnder(e.target);
    if (!ed) return;
    // 关键：不拦住的话，Qt WebEngine 会弹它自己的英文原生菜单，两张菜单会叠在一起
    e.preventDefault();
    e.stopPropagation();
    openMenu(ed, e.clientX, e.clientY);
  }, true);

  document.addEventListener('mousedown', function (e) {
    if (!menuEl) return;
    if (menuEl.contains(e.target)) return;
    closeMenu();
  }, true);

  document.addEventListener('keydown', function (e) {
    if (!menuEl) return;
    if (e.key === 'Escape' || e.keyCode === 27) {
      e.preventDefault();
      closeMenu();
    }
  }, true);

  window.addEventListener('blur', closeMenu);
  window.addEventListener('resize', closeMenu);
  document.addEventListener('scroll', closeMenu, true);

  // 给面板用的几个基本操作（剪贴板在 Python 那边，界面操作在这里）
  window.__ctutor_do = function (op, text) {
    const m = window.__ctutor_monaco;
    const list = (m && m.editor && m.editor.getEditors) ? m.editor.getEditors() : [];
    if (!list.length) return '';
    const ed = list[0], model = ed.getModel();
    if (!model) return '';
    const sel = ed.getSelection();
    const hasSel = !!(sel && !sel.isEmpty());
    try {
      if (op === 'seltext') {
        const pos = ed.getPosition();
        return JSON.stringify(hasSel ? model.getValueInRange(sel)
          : (pos ? model.getLineContent(pos.lineNumber) : ''));
      }
      if (op === 'delete') {
        if (hasSel) ed.executeEdits('ctutor-del', [{ range: sel, text: '' }]);
        else ed.trigger('keyboard', 'deleteLeft');
        ed.focus();
        return 'ok';
      }
      if (op === 'insert') {
        const pos = ed.getPosition() || { lineNumber: 1, column: 1 };
        const range = hasSel ? sel
          : new m.Range(pos.lineNumber, pos.column, pos.lineNumber, pos.column);
        ed.executeEdits('ctutor-paste', [{ range: range, text: String(text || ''),
                                           forceMoveMarkers: true }]);
        ed.focus();
        return 'ok';
      }
      if (op === 'selectall') {
        // 这个 Monaco 版本里没有 model.getFullRange()，自己拼一个全文范围
        const last = model.getLineCount();
        ed.setSelection(new m.Range(1, 1, last, model.getLineMaxColumn(last)));
        ed.focus();
        return 'ok';
      }
      if (op === 'undo') { ed.trigger('keyboard', 'undo'); return 'ok'; }
      if (op === 'redo') { ed.trigger('keyboard', 'redo'); return 'ok'; }
    } catch (e) {
      return 'error:' + e.message;
    }
    return '';
  };

  window.__ctutor_menu_items = ITEMS;
})();
</script>
"""


def build_script() -> str:
    """返回注入用的 CSS + <script>。

    汉化表只在 Python 里维护一份（MENU_ZH），这里序列化进脚本，
    免得"网页菜单"和"Qt 原生菜单"各用一张表、翻得不一样。
    """
    return (CSS
            + SCRIPT.replace("__MENU_ZH__", json.dumps(MENU_ZH, ensure_ascii=False))
                    .replace("__MENU_ITEMS__", json.dumps(MENU_ITEMS, ensure_ascii=False)))


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