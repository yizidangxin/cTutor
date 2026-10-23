#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""锚点（书签）+ 右键菜单汉化 的测试。

两条需求各自的验收点：
  1. 右键菜单**每次**都是中文 —— 包括"选中文字后菜单条目变多""取消选中再右键"
     这些 Monaco 复用同一个菜单容器的情形（原来的实现只在第一次新建菜单时翻译）。
  2. 锚点能命名、能列出、能跳转，而且**对代码零影响**：
     不加字符、不标"已修改"、不进 .c 文件，只存在会话里。

图形部分用 offscreen 真跑；装饰器数量、光标位置这些是从 Monaco 里读回来的，
不是"看起来应该对"。
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

# 配置与会话都指到临时目录：锚点会写会话文件，不隔离就会污染真实进度
_TEST_DIR = tempfile.mkdtemp(prefix="ctutor_anchor_cfg_")
from engine import config as config_mod                        # noqa: E402
from engine import session as session_mod                      # noqa: E402

config_mod.user_config_path = lambda: os.path.join(_TEST_DIR, "config.json")
try:
    config_mod.save_user_config({"ui": {"help_shown": True}})
except Exception:                                              # noqa: BLE001
    pass

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


def pump(app, ms=400):
    t0 = time.time()
    while time.time() - t0 < ms / 1000.0:
        app.processEvents()
        time.sleep(0.02)


# ---------------------------------------------------------------- 一、纯逻辑
print("— 锚点数据的存取（不需要界面）—")

clean = session_mod._clean_anchors
check("正常锚点原样保留",
      clean([{"name": "主循环", "line": 3, "text": "for(;;)"}]) ==
      [{"name": "主循环", "line": 3, "text": "for(;;)"}],
      clean([{"name": "主循环", "line": 3, "text": "for(;;)"}]))

check("行号是字符串也能纠正成整数",
      clean([{"name": "a", "line": "7", "text": ""}]) == [{"name": "a", "line": 7, "text": ""}],
      clean([{"name": "a", "line": "7", "text": ""}]))

check("坏数据被丢掉，不会拖垮会话",
      clean([None, "x", {"line": 0}, {"line": -2}, {"line": "abc"}, {"name": "ok", "line": 1}])
      == [{"name": "ok", "line": 1, "text": ""}],
      clean([None, "x", {"line": 0}, {"line": -2}, {"line": "abc"}, {"name": "ok", "line": 1}]))

check("不是列表就当作没有锚点", clean("不是列表") == [] and clean(None) == [], clean("x"))

many = [{"name": "n%d" % i, "line": i + 1, "text": ""} for i in range(session_mod.MAX_ANCHORS + 50)]
check("锚点数量有上限（防止会话文件被撑爆）",
      len(clean(many)) == session_mod.MAX_ANCHORS, len(clean(many)))

check("按行号排好序", [a["line"] for a in clean(
    [{"line": 9}, {"line": 2}, {"line": 5}])] == [2, 5, 9],
    [a["line"] for a in clean([{"line": 9}, {"line": 2}, {"line": 5}])])

# 会话往返
session_mod.save([{"path": "", "encoding": "mbcs", "label": "测试",
                   "modified": True, "text": "int main(){}",
                   "anchors": [{"name": "入口", "line": 1, "text": "int main(){}"}]}], 0)
back = session_mod.load()
check("会话里读回锚点",
      back["docs"][0]["anchors"] == [{"name": "入口", "line": 1, "text": "int main(){}"}],
      back["docs"][0].get("anchors"))



old_path = session_mod.session_path()
with open(old_path, "w", encoding="utf-8") as f:
    json.dump({"schema": session_mod.SESSION_SCHEMA, "active": 0,
               "docs": [{"path": "", "encoding": "mbcs", "label": "老版本",
                         "modified": True, "text": "int x;"}]}, f, ensure_ascii=False)
old = session_mod.load()
check("老会话文件没有 anchors 字段也能读",
      old["docs"] and old["docs"][0]["anchors"] == [],
      old["docs"][0] if old["docs"] else "空")

with open(old_path, "w", encoding="utf-8") as f:
    json.dump({"schema": session_mod.SESSION_SCHEMA, "active": 0,
               "docs": [{"path": "", "modified": True, "text": "int x;",
                         "anchors": "坏数据"}]}, f, ensure_ascii=False)
bad = session_mod.load()
check("anchors 是坏类型也不炸，退成空列表", bad["docs"][0]["anchors"] == [],
      bad["docs"][0].get("anchors"))

try:
    os.remove(old_path)
except OSError:
    pass


# ---------------------------------------------------------------- 二、界面
print()
print("— 面板里的锚点（离屏真跑 Monaco）—")

try:
    import PySide6                                        # noqa: F401
    from PySide6.QtWidgets import QApplication
    has_qt = True
except Exception as e:                                    # noqa: BLE001
    has_qt = False
    print("[SKIP] 没装 PySide6：%s" % e)

if has_qt:
    import panel

    app = QApplication.instance() or QApplication(sys.argv[:1])
    panel.ensure_cjk_font(app)

    win = panel.MainWindow()
    win.resize(1360, 880)
    win.show()
    pump(app, 1800)

    code = "int main() {\n  int a = 1;\n  int b = 2;\n  return a + b;\n}\n"

    def js(script, timeout=8.0):
        box = {}
        try:
            win.editor.widget.page().runJavaScript(script, lambda v: box.setdefault("v", v))
        except Exception:                                 # noqa: BLE001
            return None
        t0 = time.time()
        while "v" not in box and time.time() - t0 < timeout:
            app.processEvents()
            time.sleep(0.02)
        return box.get("v")

    check("编辑器是 Monaco（否则这些功能不适用）", win.editor.monaco, win.editor.note)

    if win.editor.monaco:
        win.editor.set_text(code)
        pump(app, 500)

        # ---- 右键命令注册 ----
        # 右键菜单是拿 getSupportedActions() 里的动作建的，所以按这个查才算数
        # （getAction 的 id 会被加上 vs.editor.ICodeEditor:N: 前缀，直接比 id 会漏）
        acts = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const want = ['ctutor_anchor_add','ctutor_anchor_list','ctutor_anchor_del'];
          const got = (ed.getSupportedActions() || [])
            .map(a => String(a.id)).filter(id => id.indexOf('ctutor_anchor') >= 0);
          const labels = want.map(id => {
            const hit = (ed.getSupportedActions() || []).find(a => String(a.id).indexOf(id) >= 0);
            return hit ? String(hit.label) : '';
          });
          return JSON.stringify({count: got.length, labels: labels,
                                 lang: ed.getModel().getLanguageId()});
        })()""")
        try:
            info = json.loads(acts or "{}")
        except ValueError:
            info = {}
        check("三个右键命令都注册进了可执行动作",
              info.get("count") == 3, acts)
        check("菜单上显示的是中文", info.get("labels") == ["添加锚点…", "锚点列表…", "删除本行锚点"],
              info.get("labels"))
        check("动作的适用语言就是当前语言（否则菜单里不会出现）",
              info.get("lang") == "c", info.get("lang"))

        # ---- 加锚点：不动代码、不标已修改 ----
        win._flush()
        d0 = win._doc()
        d0.saved_text, d0.modified = d0.text, False
        win._sync_tabs()
        pump(app, 120)
        text_before = win.editor.text()
        mod_before = d0.modified

        win.add_anchor(2, "  int a = 1;", name="初始化")
        win.add_anchor(4, "  return a + b;", name="返回")
        pump(app, 400)

        check("两个锚点都记下来了", len(win.anchors()) == 2, win.anchors())
        check("锚点名字对", [a["name"] for a in win.anchors()] == ["初始化", "返回"],
              [a["name"] for a in win.anchors()])
        check("代码一个字符都没动", win.editor.text() == text_before)
        check("文件没被标成已修改", win._doc().modified == mod_before,
              "%s → %s" % (mod_before, win._doc().modified))
        check("不往 .c 里插注释（内容里没有锚点字样）",
              "锚点" not in win.editor.text() and "锚点" not in win._doc().text)

        n = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const c = ed.__ctutorAnchorDeco;
          return c && c.getRanges ? c.getRanges().length : -1;
        })()""")
        check("编辑器里画出 2 个锚点标记", n == 2, n)

        # 有装饰器 ≠ 屏幕上看得见。锚点现在不画在行号区，而是贴在**编辑器最右端**
        # 的蓝色标签（.ctutor-anchor-flag），所以这里数的是标签节点，并且验证它
        # 真的在右边、点了能跳。
        dom = js("""(() => {
          const els = Array.from(document.querySelectorAll('.ctutor-anchor-flag'));
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const host = ed.getDomNode().getBoundingClientRect();
          const items = els.map(function (e) {
            const r = e.getBoundingClientRect();
            const s = getComputedStyle(e);
            return { text: (e.textContent || '').trim(),
                     right: Math.round(r.right), left: Math.round(r.left),
                     top: Math.round(r.top), w: Math.round(r.width),
                     bg: s.backgroundColor, vis: s.display };
          });
          const layer = document.querySelector('.ctutor-anchor-layer');
          const lr = layer ? layer.getBoundingClientRect() : null;
          return JSON.stringify({count: els.length,
                                 host: {left: Math.round(host.left), right: Math.round(host.right),
                                        width: Math.round(host.width)},
                                 layer: lr ? {w: Math.round(lr.width), h: Math.round(lr.height)} : null,
                                 items: items});
        })()""")
        try:
            dm = json.loads(dom or "{}")
        except ValueError:
            dm = {}
        _items = dm.get("items") or []
        check("锚点标签真的画到了屏幕上（2 个）", dm.get("count") == 2, dom)
        check("锚点标签贴在最右端（不是行号区）",
              bool(_items) and all(i.get("right", 0) >= dm.get("host", {}).get("right", 0) - 160
                                   and i.get("left", 0) > dm.get("host", {}).get("left", 0) + 300
                                   for i in _items), dom)
        # 这一条专治踩过的坑：Monaco 的 overlayWidgets 容器自身高度是 0，
        # 如果标签层跟着 inset:0，就会被裁成 0 高——坐标全对、DOM 全在，屏幕上一片空白。
        _layer = dm.get("layer") or {}
        check("标签层真的撑开了（没被父容器裁成 0 高）",
              _layer.get("h", 0) > 100 and _layer.get("w", 0) > 100, dm.get("layer"))
        check("锚点标签是蓝色的、写着名字",
              bool(_items)
              and all(i.get("bg", "").startswith("rgba(37, 99, 235") for i in _items)
              and _items[0].get("text", "").startswith("⚑ ")
              and len(_items[0].get("text", "")) > 3, _items)

        # 点标签要能跳过去
        before_line = js("window.__ctutor_monaco.editor.getEditors()[0].getPosition().lineNumber")
        js("""(() => {
          const els = document.querySelectorAll('.ctutor-anchor-flag');
          if (els.length) els[els.length - 1].click();
          return 1;
        })()""")
        pump(app, 300)
        after_line = js("window.__ctutor_monaco.editor.getEditors()[0].getPosition().lineNumber")
        check("点最右端的标签能跳到那个锚点",
              after_line == 4 and before_line != 4, "%r -> %r" % (before_line, after_line))
# 标红和锚点必须各用各的 decorations collection，否则会互相冲掉
        win.editor.mark_errors([{"line": 3, "col": 3, "message": "测试错误",
                                 "level": "error", "primary": True}])
        pump(app, 400)
        state = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const a = ed.__ctutorAnchorDeco, e = ed.__ctutorDeco;
          return JSON.stringify({
            distinct: !!(a && e && a !== e),
            anchors: a && a.getRanges ? a.getRanges().length : -1,
            marks: e && e.getRanges ? e.getRanges().length : -1
          });
        })()""")
        try:
            st = json.loads(state or "{}")
        except ValueError:
            st = {}
        check("标红和锚点各用各的 collection（互相不冲掉）", st.get("distinct") is True, state)
        check("标红之后锚点标记还在", st.get("anchors") == 2, state)
        check("标完之后标记也在", st.get("marks") >= 1, state)
        win.editor.clear_marks()
        pump(app, 200)

        # ---- 跳转 ----
        win.editor.goto_anchor(4)
        pump(app, 400)
        pos = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const p = ed.getPosition();
          return JSON.stringify({line: p.lineNumber, column: p.column});
        })()""")
        check("跳转把光标放到第 4 行行首", pos == '{"line":4,"column":1}', pos)

        # ---- 同一行重复加：改名不叠加 ----
        win.add_anchor(2, "  int a = 1;", name="初始化（改）")
        pump(app, 200)
        check("同一行重复加只改名，不新增", len(win.anchors()) == 2, len(win.anchors()))
        check("名字确实被更新", win.anchors()[0]["name"] == "初始化（改）",
              win.anchors()[0]["name"])

        # ---- 删除 ----
        check("删除本行锚点", win.remove_anchors_at(2) == 1 and len(win.anchors()) == 1,
              win.anchors())
        check("删没有锚点的行返回 0", win.remove_anchors_at(99) == 0)
        pump(app, 300)
        n2 = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const c = ed.__ctutorAnchorDeco;
          return c && c.getRanges ? c.getRanges().length : -1;
        })()""")
        check("删掉后编辑器里只剩 1 个标记", n2 == 1, n2)

        # ---- 会话往返（经过面板，不是直接调 session） ----
        win._save_session()
        back = session_mod.load()
        got = (back.get("docs") or [{}])[0].get("anchors") or []
        check("锚点写进会话文件", [a["name"] for a in got] == ["返回"], got)

        # ---- 切标签后锚点跟着走 ----
        win._doc().anchors = [{"name": "返回", "line": 4, "text": "  return a + b;"}]
        win._sync_anchors()
        pump(app, 300)
        n3 = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const c = ed.__ctutorAnchorDeco;
          return c && c.getRanges ? c.getRanges().length : -1;
        })()""")
        check("重新同步后标记数正确", n3 == 1, n3)

        # ---- 右键命令 → Python ----
        hits = []
        orig = win.show_anchors
        win.show_anchors = lambda: hits.append("list")
        try:
            js("""(() => {
              const ed = window.__ctutor_monaco.editor.getEditors()[0];
              ed.trigger('keyboard', 'ctutor_anchor_list', {});
              return 1;
            })()""")
            pump(app, 600)
        finally:
            win.show_anchors = orig
        check("点右键菜单里的命令能回到 Python", hits == ["list"], hits)

        # ---- 右键菜单汉化：三种情形 ----
        js("""(() => {
          const host = document.createElement('div');
          host.className = 'context-view __anchor_probe_menu';
          host.innerHTML = '<div class="monaco-menu"><ul>' +
            '<li><a class="action-label">Cut</a></li>' +
            '<li><a class="action-label">Copy</a></li>' +
            '</ul></div>';
          document.body.appendChild(host);
          return 1;
        })()""")
        pump(app, 500)
        first = js("""(() => JSON.stringify(Array.from(
            document.querySelectorAll('.__anchor_probe_menu .action-label'))
            .map(e => (e.textContent||'').trim())))()""")
        check("第一次右键：菜单是中文", first == '["剪切","复制"]', first)

        # Monaco 第二次开菜单不新建容器，只换里面的条目
        js("""(() => {
          const words = ['Undo', 'Redo'];
          document.querySelectorAll('.__anchor_probe_menu .action-label')
            .forEach((el, i) => { el.textContent = words[i]; });
          return 1;
        })()""")
        pump(app, 600)
        second = js("""(() => JSON.stringify(Array.from(
            document.querySelectorAll('.__anchor_probe_menu .action-label'))
            .map(e => (e.textContent||'').trim())))()""")
        check("复用同一个菜单容器（第二次右键）也是中文", second == '["撤销","重做"]', second)

        # 只改文字节点、不重建元素
        js("""(() => {
          document.querySelector('.__anchor_probe_menu .action-label').firstChild.nodeValue = 'Paste';
          return 1;
        })()""")
        pump(app, 600)
        third = js("""(() => JSON.stringify(Array.from(
            document.querySelectorAll('.__anchor_probe_menu .action-label'))
            .map(e => (e.textContent||'').trim())))()""")
        check("只改文字节点（Monaco 真实做法）也能翻成中文",
              third == '["粘贴","重做"]', third)

        # 中文菜单项不该被二次改写，未登记的英文也不动
        js("""(() => {
          const els = document.querySelectorAll('.__anchor_probe_menu .action-label');
          els[1].textContent = 'Some Unknown Action';
          return 1;
        })()""")
        pump(app, 500)
        fourth = js("""(() => JSON.stringify(Array.from(
            document.querySelectorAll('.__anchor_probe_menu .action-label'))
            .map(e => (e.textContent||'').trim())))()""")
        check("没登记过的英文项保持原样", fourth == '["粘贴","Some Unknown Action"]', fourth)

        # 真实结构之二：快捷键和文字在同一个元素里
        # <a class="action-label">Cut<span class="keybinding">Ctrl+X</span></a>
        # 整块覆盖 textContent 会把快捷键提示抹掉，所以必须只改文字节点。
        js("""(() => {
          const host = document.createElement('div');
          host.className = 'context-view __anchor_probe_menu2';
          host.innerHTML = '<div class="monaco-menu"><ul>' +
            '<li><a class="action-label">Cut<span class="keybinding">Ctrl+X</span></a></li>' +
            '<li><a class="action-label">Paste</a></li>' +
            '</ul></div>';
          document.body.appendChild(host);
          return 1;
        })()""")
        pump(app, 600)
        nested = js("""(() => {
          const a = document.querySelector('.__anchor_probe_menu2 .action-label');
          const kb = a.querySelector('.keybinding');
          return JSON.stringify({text: (a.textContent || '').trim(),
                                 kb: kb ? kb.textContent : ''});
        })()""")
        try:
            ns = json.loads(nested or "{}")
        except ValueError:
            ns = {}
        check("带快捷键的条目也翻成中文（文字变了）",
              ns.get("text") == "剪切Ctrl+X", nested)
        check("快捷键提示没被翻译抹掉", ns.get("kb") == "Ctrl+X", nested)

        # 翻译完又被 Monaco 打回英文：轮询必须在下一拍再翻回来（不依赖任何事件）
        js("""(() => {
          document.querySelectorAll('.__anchor_probe_menu2 .action-label')
            .forEach(el => { if (el.firstChild && el.firstChild.nodeType === 3) {
              el.firstChild.nodeValue = el.firstChild.nodeValue.replace('粘贴', 'Paste')
                .replace('剪切', 'Cut'); } });
          return 1;
        })()""")
        pump(app, 800)
        again = js("""(() => JSON.stringify(Array.from(
            document.querySelectorAll('.__anchor_probe_menu2 .action-label'))
            .map(e => (e.textContent || '').trim())))()""")
        check("被打回英文后 0.8 秒内自动翻回中文",
              again == '["剪切Ctrl+X","粘贴"]', again)
        js("document.querySelectorAll('.__anchor_probe_menu2').forEach(e => e.remove()); 1")

        # ---------------- 自己画的右键菜单（真正生效的那条路） ----------------
        # 这个 monaco-core 没有右键菜单贡献，右键请求会被 Qt 接走弹英文原生菜单。
        # 所以页面里 preventDefault 掉原生菜单、自己画中文菜单。这里验证那条路。
        # 用真实的 .c 文件：菜单命令注册时带了"语言是 C"的前置条件，
        # 拿示例文档（语言不是 C）测会测不出问题。
        mfile = os.path.join(_TEST_DIR, "menu_demo.c")
        with open(mfile, "w", encoding="utf-8") as f:
            f.write("int main(void) {\n  int a = 1;\n  return a;\n}\n")
        win._open_path(mfile)
        pump(app, 1200)
        opened = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const node = ed.getDomNode().querySelector('.overflow-guard') || ed.getDomNode();
          const r = node.getBoundingClientRect();
          const ev = new MouseEvent('contextmenu', {
            bubbles: true, cancelable: true, view: window, button: 2, buttons: 2,
            clientX: Math.round(r.left + 60), clientY: Math.round(r.top + 40)
          });
          const notCancelled = node.dispatchEvent(ev);
          const menus = Array.from(document.querySelectorAll('.ctutor-menu'));
          const labels = menus.length ? Array.from(
            menus[0].querySelectorAll('.ctutor-menu-label')).map(e => e.textContent) : [];
          return JSON.stringify({notCancelled: notCancelled, menus: menus.length,
                                 labels: labels,
                                 open: !!window.__ctutor_menu_open});
        })()""")
        try:
            om = json.loads(opened or "{}")
        except ValueError:
            om = {}
        check("右键在页面里自己弹菜单（不再交给 Qt 的原生英文菜单）",
              om.get("menus") == 1 and om.get("notCancelled") is False, opened)
        _labels = om.get("labels") or []
        _english = [x for x in _labels if any(ch.isascii() and ch.isalpha() for ch in x)]
        check("菜单全是中文（没有一个英文单词）",
              len(_labels) >= 8 and not _english, _labels)
        check("菜单里有剪切/复制/粘贴/全选",
              all(k in _labels for k in ("剪切", "复制", "粘贴", "全选")), _labels)
        check("菜单里有三条锚点命令",
              all(any(k in x for x in _labels) for k in ("添加锚点", "锚点列表", "删除本行锚点")),
              _labels)

        # 点「全选」必须真的选中全部内容
        js("""(() => {
          const row = document.querySelector('.ctutor-menu-item[data-id="ctutor_selectall"]');
          if (row) row.click();
          return 1;
        })()""")
        pump(app, 900)
        sel = js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const s = ed.getSelection(), m = ed.getModel();
          return JSON.stringify({from: s.startLineNumber + ':' + s.startColumn,
                                 to: s.endLineNumber + ':' + s.endColumn,
                                 len: m.getValueInRange(s).length,
                                 total: m.getValueLength(),
                                 menu: document.querySelectorAll('.ctutor-menu').length});
        })()""")
        try:
            sm = json.loads(sel or "{}")
        except ValueError:
            sm = {}
        check("点「全选」真的全选了", sm.get("len") == sm.get("total") and sm.get("len", 0) > 10, sel)
        check("点完菜单自己关掉了", sm.get("menu") == 0, sel)

        # 复制：走 Python 的剪贴板
        QApplication.clipboard().setText("")
        js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const mm = ed.getModel(), last = mm.getLineCount();
          ed.setSelection(new window.__ctutor_monaco.Range(1, 1, last, mm.getLineMaxColumn(last)));
          const node = ed.getDomNode().querySelector('.overflow-guard') || ed.getDomNode();
          const r = node.getBoundingClientRect();
          node.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,
            view: window, button: 2, buttons: 2,
            clientX: Math.round(r.left + 60), clientY: Math.round(r.top + 40)}));
          const row = document.querySelector('.ctutor-menu-item[data-id="ctutor_copy"]');
          if (!row) return 'no-row';
          row.click();
          return 'clicked';
        })()""")
        pump(app, 1500)
        clip = QApplication.clipboard().text() or ""
        check("点「复制」把选中内容放进了系统剪贴板", clip.startswith("int main"), clip[:40])

        # 粘贴：从系统剪贴板插进去
        QApplication.clipboard().setText("/* 粘贴测试 */")
        js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          ed.setPosition({lineNumber: 1, column: 1});
          const node = ed.getDomNode().querySelector('.overflow-guard') || ed.getDomNode();
          const r = node.getBoundingClientRect();
          node.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,
            view: window, button: 2, buttons: 2,
            clientX: Math.round(r.left + 60), clientY: Math.round(r.top + 40)}));
          const row = document.querySelector('.ctutor-menu-item[data-id="ctutor_paste"]');
          if (row) row.click();
          return 1;
        })()""")
        pump(app, 1200)
        pasted = win.editor.text()
        check("点「粘贴」把剪贴板内容插进了编辑器", pasted.startswith("/* 粘贴测试 */"), pasted[:40])

        # Esc 关菜单
        js("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const node = ed.getDomNode().querySelector('.overflow-guard') || ed.getDomNode();
          const r = node.getBoundingClientRect();
          node.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,
            view: window, button: 2, buttons: 2,
            clientX: Math.round(r.left + 60), clientY: Math.round(r.top + 40)}));
          return document.querySelectorAll('.ctutor-menu').length;
        })()""")
        pump(app, 200)
        js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); 1")
        pump(app, 200)
        left = js("document.querySelectorAll('.ctutor-menu').length")
        check("按 Esc 能关掉菜单", left == 0, left)

        # 结构完全不同的菜单：条目没有 .action-label 类，只有纯文字。
        # 汉化不能靠类名，否则换个 Monaco 版本就全失效。
        js("""(() => {
          const host = document.createElement('div');
          host.className = 'context-view __anchor_probe_menu3';
          host.innerHTML = '<div class="monaco-menu"><ul>' +
            '<li class="whatever">Cut</li>' +
            '<li class="whatever"><span>Redo</span></li>' +
            '</ul></div>';
          document.body.appendChild(host);
          return 1;
        })()""")
        pump(app, 700)
        noclass = js("""(() => JSON.stringify(Array.from(
            document.querySelectorAll('.__anchor_probe_menu3 li'))
            .map(e => (e.textContent || '').trim())))()""")
        check("没有 action-label 类名也能翻（不依赖结构）",
              noclass == '["剪切","重做"]', noclass)
        js("document.querySelectorAll('.__anchor_probe_menu3').forEach(e => e.remove()); 1")

        check("抓到了真实菜单的 HTML 快照（出问题时可对着改）",
              (js("(window.__ctutor_menu_dump || '').length") or 0) > 50,
              js("(window.__ctutor_menu_dump || '').length"))

        js("document.querySelectorAll('.__anchor_probe_menu').forEach(e => e.remove()); 1")

    # ---- 锚点列表对话框 ----
    from PySide6.QtWidgets import QDialog
    dlg = panel.AnchorDialog([{"name": "主循环", "line": 3, "text": "for(;;)"}], win)
    check("锚点列表显示名字和行号",
          "主循环" in dlg.list.item(0).text() and "第 3 行" in dlg.list.item(0).text(),
          dlg.list.item(0).text())
    check("有锚点时按钮可用", dlg.btn_goto.isEnabled() and dlg.btn_del.isEnabled())
    dlg2 = panel.AnchorDialog([], win)
    check("没有锚点时按钮变灰（不会点了没反应）",
          not dlg2.btn_goto.isEnabled() and not dlg2.btn_del.isEnabled())
    dlg2.deleteLater()
    dlg.deleteLater()

    # 按钮真的接上了
    check("工具栏有「锚点」按钮", hasattr(win, "btn_anchor") and win.btn_anchor.text() == "锚点")
    _hits = []
    _orig = win.show_anchors
    win.show_anchors = lambda: _hits.append(1)
    try:
        win.btn_anchor.click()
        pump(app, 100)
    finally:
        win.show_anchors = _orig
    check("「锚点」按钮点下去真的会打开列表", _hits == [1], _hits)

    # ---- 关掉再开：锚点要跟着「上次进度」一起回来 ----
    # 用真实文件来验：没有路径、也没保存过的文档本来就不会被会话记住，
    # 那种标签重开就没了，锚点跟着走是合理的（不是锚点丢了，是整个标签没了）。
    cfile = os.path.join(_TEST_DIR, "anchor_demo.c")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(code)
    win._open_path(cfile)
    pump(app, 500)
    win._doc().anchors = [{"name": "返回", "line": 4, "text": "  return a + b;"}]
    win._sync_anchors()
    pump(app, 200)
    win._save_session()
    # ---- 现场快照：写进临时目录，含"还剩哪些英文" ----
    # 只写第一次，所以先清掉可能已经被上面的测试写出来的那份
    try:
        if os.path.exists(win.ctx_menu_dump_path()):
            os.remove(win.ctx_menu_dump_path())
    except OSError:
        pass
    win._write_ctx_dump("网页菜单（测试）", ["剪切", "Copy As"], "<div>probe</div>")
    dump_path = win.ctx_menu_dump_path()
    body = ""
    if os.path.exists(dump_path):
        body = open(dump_path, encoding="utf-8").read()
    check("菜单现场快照写到了临时目录", "来源: 网页菜单（测试）" in body, dump_path)
    check("快照里标出了仍是英文的条目",
          "仍是英文的条目" in body and "Copy As" in body, body[:200])
    try:
        os.remove(dump_path)
    except OSError:
        pass

    # 前面的粘贴测试把文档标成"已修改"了。关窗口会弹「要不要保存」的模态框，
    # 离屏跑测试时那个框没人点，就会一直卡住——先把改动标记清掉再关。
    for _d in list(win.docs):
        _d.modified = False
    win._dirty_timer.stop()
    win._ctx_timer.stop()
    win.close()
    pump(app, 300)

    win2 = panel.MainWindow()
    win2.resize(1360, 880)
    win2.show()
    pump(app, 3000)                      # 等 Monaco 加载完 + 补画锚点
    check("重开窗口后锚点还在", [a["name"] for a in win2.anchors()] == ["返回"],
          win2.anchors())
    n_restore = -1
    if win2.editor.monaco:
        box = {}
        win2.editor.widget.page().runJavaScript("""(() => {
          const ed = window.__ctutor_monaco.editor.getEditors()[0];
          const c = ed.__ctutorAnchorDeco;
          return c && c.getRanges ? c.getRanges().length : -1;
        })()""", lambda v: box.setdefault("v", v))
        t0 = time.time()
        while "v" not in box and time.time() - t0 < 8:
            app.processEvents()
            time.sleep(0.02)
        n_restore = box.get("v")
    check("重开窗口后编辑器里也画出来了", n_restore == 1, n_restore)
    win2._dirty_timer.stop()
    win2._ctx_timer.stop()

n_fail = sum(1 for _, ok in results if not ok)
print()
print("=" * 66)
print("锚点与右键菜单测试：%d 项，通过 %d，失败 %d"
      % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)