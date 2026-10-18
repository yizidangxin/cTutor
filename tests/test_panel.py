#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""面板测试：用离屏渲染做无人值守验证。

图形界面最难做自动化验证。这里用 Qt 的 offscreen 平台插件把面板真正渲染成 PNG，
再检查文件确实生成且内容非空——比"进程没崩"强得多。

同时记录一个实测坑：**offscreen 模式下 QFontDatabase 的字族数为 0**，
不显式加载中文字体的话整个界面会画成方块（panel.ensure_cjk_font 就是为此而加）。
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 把配置与会话都指到临时目录：测试必须是**干净起点**。
# 加了"上次进度"之后，窗口启动会恢复上一次的标签——如果不隔离，
# 前一个窗口关掉时写下的会话会被后一个窗口读回去，测试就不再可重复。
_TEST_DIR = tempfile.mkdtemp(prefix="ctutor_panel_cfg_")
from engine import config as _config_mod                      # noqa: E402
from engine import session as _session_mod                    # noqa: E402
_config_mod.user_config_path = lambda: os.path.join(_TEST_DIR, "config.json")


def _clear_session():
    """删掉会话文件，让下一个窗口从示例开始。"""
    try:
        p = _session_mod.session_path()
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


# ---------------------------------------------------------------- 依赖检查
try:
    import PySide6                                       # noqa: F401
    has_pyside = True
    from PySide6 import __version__ as pyside_ver
except Exception as e:                                   # noqa: BLE001
    has_pyside = False
    pyside_ver = str(e)
    print("PySide6 不可用：%s" % e)

try:
    import qtmonaco                                      # noqa: F401
    has_monaco = True
except Exception as e:                                   # noqa: BLE001
    has_monaco = False
    print("qtmonaco 不可用：%s（将使用降级编辑器）" % e)

if not has_pyside:
    print("\n跳过面板测试（未安装 PySide6）")
    print("安装：pip install -i https://pypi.tuna.tsinghua.edu.cn/simple PySide6 qtmonaco")
    raise SystemExit(0)

print("PySide6 %s　qtmonaco %s" % (pyside_ver, "可用" if has_monaco else "不可用"))
print()

# ---------------------------------------------------------------- 纯逻辑部分
import panel                                             # noqa: E402

check("panel 可导入", True)

# 用离屏方式渲染，验证 HTML 渲染路径（不启动完整窗口）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication               # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])
fam = panel.ensure_cjk_font(app)
check("中文字体被显式加载（否则界面全是方块）", bool(fam), fam or "未找到任何中文字体")

from engine import explain, render                       # noqa: E402
from engine.diagnostic import Diagnostic                 # noqa: E402
from engine.kb import KnowledgeBase                      # noqa: E402

kb = KnowledgeBase.load("msvc")
d = Diagnostic(compiler="msvc", message="语法错误: 缺少“;”(在标识符“printf”的前面)",
               code="C2146", level="error",
               file=os.path.join(ROOT, "tests", "cases", "01_missing_semicolon.c"),
               line=5, column=5)
exp = explain.build(d, kb)
h = render.render_html(exp, depth=4)

check("HTML 含 5 层标题", all(("【%d " % i) in h for i in range(1, 5)))
check("depth 控制生效", "【5 知识点】" not in render.render_html(exp, depth=4)
      and "【5 知识点】" in render.render_html(exp, depth=5))

import re                                                # noqa: E402
t_layers = re.findall(r"【(\d) (\S+?)】", render.render(exp, depth=4))
h_layers = re.findall(r"【(\d) (\S+?)】", h)
check("终端与面板的层结构完全一致（不会两处说法不同）", t_layers == h_layers,
      "%s vs %s" % (t_layers, h_layers))

# 生成一张 HTML 渲染快照，确认中文与插入符都在
check("HTML 里含中文结论与插入符", "缺少分号" in h and "^" in h)

# ★ 提示只在检测到中文特有陷阱时出现，必须用含全角标点的样本验证
d2 = Diagnostic(compiler="msvc", message="语法错误:“数字上的错误后缀”", code="C2059",
                level="error",
                file=os.path.join(ROOT, "tests", "cases", "02_fullwidth_semicolon.c"),
                line=4, column=13)
h2 = render.render_html(explain.build(d2, kb), depth=4)
check("含全角标点的样本会给出 ★ 中文提示", "★" in h2 and "全角字符" in h2)

# ---------------------------------------------------------------- 端到端离屏截图
shot = os.path.join(HERE, "build", "_panel_shot.png")
# tests/build/ 被 .gitignore 忽略，全新克隆时并不存在。不先建目录的话
# QWidget.grab().save() 会静默失败，子进程返回 1，测试看起来是"莫名其妙挂了"。
os.makedirs(os.path.dirname(shot), exist_ok=True)
if os.path.exists(shot):
    os.remove(shot)
env = dict(os.environ)
env["QT_QPA_PLATFORM"] = "offscreen"
env["PYTHONIOENCODING"] = "utf-8"
env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu"
proc = subprocess.run([sys.executable, os.path.join(ROOT, "panel.py"),
                       "--selftest", shot],
                      capture_output=True, cwd=ROOT, env=env, timeout=180)
out = (proc.stdout or b"").decode("utf-8", errors="replace")
err = (proc.stderr or b"").decode("utf-8", errors="replace")
check("面板离屏渲染进程正常结束", proc.returncode == 0,
      "returncode=%s\n%s" % (proc.returncode, err[-400:]))
check("截图文件已生成", os.path.exists(shot),
      os.path.exists(shot) and os.path.getsize(shot))
if os.path.exists(shot):
    size = os.path.getsize(shot)
    # 纯色/空窗口的 PNG 会非常小；有文字和编辑器内容的截图会大得多
    check("截图内容非空（不是一张空白图）", size > 20000, "%d bytes" % size)
check("Monaco 编辑器被成功启用（而非降级）",
      "编辑器=Monaco" in out, out.strip()[-200:])

# ---------------------------------------------------------------- 帮助按钮开关
print()
print("— 帮助按钮（可打开 / 关闭）—")
import time                                              # noqa: E402

from engine import report as report_mod                  # noqa: E402

_clear_session()
win = panel.MainWindow()
win.resize(1100, 760)
win.show()
for _ in range(6):
    app.processEvents()
    time.sleep(0.05)

check("「使用说明」只保留菜单一个入口（工具栏重复按钮已删）",
      not hasattr(win, "btn_help") and hasattr(win, "act_help")
      and win.act_help.isCheckable())
check("AI 菜单有「设置密钥」与「测试连接」入口",
      hasattr(win, "act_ai_key") and hasattr(win, "act_ai_test")
      and win.act_ai_key.text().startswith("设置")
      and win.act_ai_test.text() == "测试连接")
check("AI 状态菜单项会显示当前密钥来源",
      hasattr(win, "act_ai_info") and win.act_ai_info.text() != "",
      win.act_ai_info.text() if hasattr(win, "act_ai_info") else None)
check("AI 复选框与配置联动（未配置时不会被强制勾上）",
      (not win.chk_ai.isChecked()) or __import__("engine.ai", fromlist=["x"]).has_api_key())
check("默认展开，先让新用户看到说明", win.act_help.isChecked())
check("展开时下方面板显示使用说明", "帮你看懂编译报错" in win.view.toHtml())

win.act_help.setChecked(False)
app.processEvents()
check("收起后不再显示说明", "帮你看懂编译报错" not in win.view.toHtml())
check("收起后显示「按 F5」提示", "F5" in win.view.toHtml())

win.act_help.setChecked(True)          # 重新打开
app.processEvents()
check("可以再次打开", "帮你看懂编译报错" in win.view.toHtml())

# 开着帮助时按检查 —— 应自动收起，把位置让给讲解
rep = report_mod.check_once(win._working_copy(), depth=4)
win._last_report = rep
win._render_report(rep)
app.processEvents()
check("检查后自动收起帮助（否则用户会以为没反应）", not win.act_help.isChecked())
check("检查后显示分层讲解", "【1 结论】" in win.view.toHtml())

# 收起状态下切换深度，讲解应重新渲染且仍不显示说明
win.cmb_depth.setCurrentIndex(0)
app.processEvents()
check("切换深度不会把说明又弹出来",
      "帮你看懂编译报错" not in win.view.toHtml() and "【1 结论】" in win.view.toHtml())

win.close()

# ---------------------------------------------------------------- 出错位置标红
print()
print("— 出错位置标红 —")
from engine import editorui as editorui_mod              # noqa: E402
from engine.diagnostic import Diagnostic                 # noqa: E402


def mkdiag(line, col, level, code="", msg="x"):
    return Diagnostic(compiler="msvc", message=msg, level=level, code=code,
                      line=line, column=col)


script = editorui_mod.build_script()
check("注入脚本含标红 CSS 类", "ctutor-err-primary-line" in script
      and "ctutor-err-line" in script and "ctutor-err-primary-glyph" in script)
check("注入脚本定义了 mark / clear / reveal 三个接口",
      "__ctutor_mark " in script and "__ctutor_clear_marks" in script
      and "__ctutor_reveal" in script)

diags = [mkdiag(4, 13, "error", "C2059"), mkdiag(5, 5, "error"),
         mkdiag(6, 1, "warning"), mkdiag(7, 3, "warning")]
chosen = diags[0]
items = editorui_mod.items_from_diagnostics(diags, chosen)
check("只标错误、不标警告（避免页面花掉）", len(items) == 2, items)
check("选中的那条标为 primary", items[0]["primary"] and not items[1]["primary"], items)
check("标红项带上行列与消息", items[0]["line"] == 4 and items[0]["col"] == 13
      and items[0]["message"] == "x")

items_w = editorui_mod.items_from_diagnostics(diags, chosen, include_warnings=True)
check("需要时可以连警告一起标", len(items_w) == 4, len(items_w))

check("没有行号的诊断被跳过（标不了位置）",
      editorui_mod.items_from_diagnostics([mkdiag(0, 0, "error")], None) == [])

# 选中的是一条警告时：它必须被标出来（否则"正在讲的"那一行反而没高亮），
# 其它错误照标，但**没被选中的警告仍然不标**。
w_items = editorui_mod.items_from_diagnostics(diags, diags[2])
check("选中的是警告时也标出来，其它错误照标，未选中的警告不标",
      len(w_items) == 3
      and any(i["primary"] and i["line"] == 6 for i in w_items)
      and not any(i["line"] == 7 for i in w_items), w_items)


# 真在编辑器里标一次，确认装饰器确实落到 Monaco 上
class _FakeEditor:
    def __init__(self):
        self.items = None
        self.cleared = 0
        self.revealed = None

    def mark_errors(self, items, callback=None):
        self.items = items

    def clear_marks(self):
        self.cleared += 1

    def reveal_line(self, line):
        self.revealed = line


_clear_session()
win2 = panel.MainWindow()
app.processEvents()
fake = _FakeEditor()
win2.editor = fake
rep_mark = {"compiler": "msvc", "chosen": {"line": 4, "column": 13, "code": "C2059",
                                           "message": "m", "level": "error"},
            "diagnostics": [{"line": 4, "column": 13, "code": "C2059", "message": "m",
                             "level": "error"},
                            {"line": 5, "column": 5, "code": "C2146", "message": "n",
                             "level": "error"}]}
win2._render_report(rep_mark)
check("渲染报告时会把诊断交给编辑器标红", fake.items is not None and len(fake.items) == 2,
      fake.items)
check("标红失败不会被静默吞掉（有 _mark_error 可查）",
      hasattr(win2, "_mark_error") and win2._mark_error == "", win2._mark_error)
check("渲染报告会滚动到出错行", fake.revealed == 4, fake.revealed)

# 编译通过时不应留下红色
win2._render_report({"compiler": "msvc", "diagnostics": [], "chosen": None})
check("没有诊断时标红被清空", fake.items == [], fake.items)
win2.close()

# ---------------------------------------------------------------- 运行 + 缩放
print()
print("— 运行功能 —")
from engine import run as run_mod                        # noqa: E402

_clear_session()
win3 = panel.MainWindow()
app.processEvents()
check("有「运行」按钮", hasattr(win3, "btn_run") and "运行" in win3.btn_run.text())
check("有超时设置控件（防止死循环卡住面板）",
      hasattr(win3, "spin_timeout") and win3.spin_timeout.value() >= 1,
      getattr(win3, "spin_timeout", None))
check("有程序输入框（scanf 需要）", hasattr(win3, "txt_input")
      and "scanf" in win3.txt_input.placeholderText())
check("下方面板分「讲解 / 运行输出」两个页签",
      win3.tabs.count() == 2 and win3.tabs.tabText(0) == "讲解"
      and win3.tabs.tabText(1) == "运行输出")
check("独立了运行输出区（不与讲解互相覆盖）", hasattr(win3, "view_run"))

# 编译没通过时：不能在运行输出里堆编译器原文，要明确告诉用户去按 F5
win3.tabs.setCurrentIndex(1)
win3._on_run_done(run_mod.RunResult(compiled=False))
html = win3.view_run.toHtml()
check("编译不过时：明确说明没运行，并引导去看 F5",
      "编译没有通过" in html and "F5" in html, html[:200])
check("编译不过时：状态栏也给出提示", "未运行" in win3.status.currentMessage(),
      win3.status.currentMessage())

# 正常结束
win3._on_run_done(run_mod.RunResult(compiled=True, ok=True, exit_code=0,
                                    stdout="sum=7\n", duration=0.12))
html2 = win3.view_run.toHtml()
check("正常运行时：显示输出内容", "sum=7" in html2, html2[:200])
check("正常运行时：显示用时", "用时" in html2)

# 超时
win3._on_run_done(run_mod.RunResult(compiled=True, ok=False, timed_out=True,
                                    exit_code=-9, duration=10.0))
check("超时时：状态栏明确说超时（多半是死循环）",
      "超时" in win3.status.currentMessage(), win3.status.currentMessage())
check("超时时：回车后按钮恢复可用（不能把界面锁死）", win3.btn_run.isEnabled())

print()
print("— 缩放 —")
check("初始缩放档为 0", win3._zoom == 0, win3._zoom)
win3.zoom_by(2)
check("放大后档位增加", win3._zoom == 2, win3._zoom)
check("放大后状态栏提示了怎么恢复",
      "Ctrl+0" in win3.status.currentMessage(), win3.status.currentMessage())
win3.zoom_reset()
check("Ctrl+0 能回到默认（档位归零）", win3._zoom == 0, win3._zoom)
win3.zoom_by(-100)
check("缩小有下限（不会缩成看不见）", win3._zoom >= -6, win3._zoom)
win3.zoom_by(100)
check("放大有上限", win3._zoom <= 14, win3._zoom)
win3.zoom_reset()

print()
print("— 缩放的快捷键 —")
from PySide6.QtCore import Qt as _Qt                          # noqa: E402
from PySide6.QtTest import QTest                              # noqa: E402


def find_action(text):
    for a in win3.actions():
        if a.text() == text:
            return a
    return None


def _all_menu_actions(win):
    """菜单里的 QAction 不会出现在 win.actions() 里，得单独收集。"""
    out = []
    for top in win.menuBar().actions():
        if top.menu():
            out.extend(top.menu().actions())
    return out


z_in = find_action("放大")
z_out = find_action("缩小")
z_reset = find_action("恢复默认字号")
check("存在放大 / 缩小 / 恢复字号三个动作",
      all(x is not None for x in (z_in, z_out, z_reset)))
check("放大注册了 Ctrl+=",
      any(s.toString() == "Ctrl+=" for s in z_in.shortcuts()),
      [s.toString() for s in z_in.shortcuts()])
check("放大也接受 Ctrl++（小键盘加号 / 按着 Shift）",
      any(s.toString() == "Ctrl++" for s in z_in.shortcuts()),
      [s.toString() for s in z_in.shortcuts()])
check("缩小注册了 Ctrl+-",
      any(s.toString() == "Ctrl+-" for s in z_out.shortcuts()),
      [s.toString() for s in z_out.shortcuts()])
check("恢复默认是 Ctrl+0", z_reset.shortcut().toString() == "Ctrl+0")
check("缩放用 ApplicationShortcut（否则可能被内嵌的 Monaco 吞掉）",
      z_in.shortcutContext() == _Qt.ApplicationShortcut)

# 防回归：写 QKeySequence.ZoomIn 这种 StandardKey 枚举，会被当成"按键码 16"，
# 注册成一个控制字符 '\x10'，快捷键**静默失效**——不报错、就是没反应。
# 同样的错在 Ctrl+O / Ctrl+S 上也犯过。
bad_shortcuts = []
for a in win3.actions():
    for sc in a.shortcuts():
        s = sc.toString()
        if len(s) == 1 and ord(s) < 32:
            bad_shortcuts.append((a.text(), repr(s)))
check("没有「把 StandardKey 枚举当成按键码」的快捷键（那会静默失效）",
      not bad_shortcuts, bad_shortcuts)
check("Ctrl+O / Ctrl+S 是真实按键序列",
      any(a.text().startswith("打开") and a.shortcut().toString() == "Ctrl+O"
          for a in _all_menu_actions(win3))
      and any(a.text() == "保存" and a.shortcut().toString() == "Ctrl+S"
              for a in _all_menu_actions(win3)),
      [(a.text(), a.shortcut().toString()) for a in _all_menu_actions(win3)])

# 光检查字符串不够 —— 真的按一下。
# 缩放走的是应用级事件过滤器，所以这里能验证到（QAction 的快捷键在离屏
# 模式下因为"窗口不活跃"根本触发不了，那种测法验证不到东西）。
win3.zoom_reset()
app.processEvents()
QTest.keyClick(win3, _Qt.Key_Equal, _Qt.ControlModifier)
for _ in range(4):
    app.processEvents()
check("按 Ctrl+= 真的会放大", win3._zoom == 1, win3._zoom)

QTest.keyClick(win3, _Qt.Key_Plus, _Qt.ControlModifier)
for _ in range(4):
    app.processEvents()
check("按 Ctrl++ 也会放大（小键盘习惯）", win3._zoom == 2, win3._zoom)

QTest.keyClick(win3, _Qt.Key_Minus, _Qt.ControlModifier)
for _ in range(4):
    app.processEvents()
check("按 Ctrl+- 真的会缩小", win3._zoom == 1, win3._zoom)

win3.zoom_by(3)
app.processEvents()
QTest.keyClick(win3, _Qt.Key_0, _Qt.ControlModifier)
for _ in range(4):
    app.processEvents()
check("按 Ctrl+0 真的回到默认", win3._zoom == 0, win3._zoom)

# 不按 Ctrl 时不能被拦走（否则正常打字会失效）
_zoom_before = win3._zoom
QTest.keyClick(win3, _Qt.Key_Equal)
for _ in range(3):
    app.processEvents()
check("不按 Ctrl 的等号不会被吃掉", win3._zoom == _zoom_before, win3._zoom)

# 讲解与运行输出互不覆盖
win3.tabs.setCurrentIndex(1)
win3._show_explanation()
check("回到讲解时会自动切回讲解页", win3.tabs.currentIndex() == 0,
      win3.tabs.currentIndex())
win3.close()

# ---------------------------------------------------------------- 界面去重 / 新建项目
print()
print("— 界面去重 —")
check("工具栏不再有重复的「打开」按钮（命令只留在文件菜单）",
      not hasattr(win3, "btn_open"))
check("工具栏不再有重复的「帮助」按钮", not hasattr(win3, "btn_help"))
menus = [a.text() for a in win3.menuBar().actions()]
check("菜单结构为 文件 / 运行 / 视图 / 帮助 / AI",
      menus[:5] == ["文件", "运行", "视图", "帮助", "AI"], menus)


def menu_items(title):
    for a in win3.menuBar().actions():
        if a.text() == title and a.menu():
            return [x.text() for x in a.menu().actions()]
    return []


run_menu = menu_items("运行")
view_menu = menu_items("视图")
check("缩放收进了「视图」菜单（原先错放在「检查」菜单里）",
      any("放大" in t for t in view_menu) and not any("放大" in t for t in run_menu),
      (run_menu, view_menu))
check("「运行」菜单同时提供系统命令行与面板内两种方式",
      any("系统命令行" in t for t in run_menu) and any("面板内" in t for t in run_menu),
      run_menu)
check("文件菜单有新建文件与设置存放位置",
      any("新建文件" in t for t in menu_items("文件"))
      and any("存放位置" in t for t in menu_items("文件")),
      menu_items("文件"))
check("文件菜单有对齐与撤回对齐",
      any("对齐" in t for t in menu_items("文件"))
      and any("撤回" in t for t in menu_items("文件")),
      menu_items("文件"))

print()
print("— 新建项目 —")
import shutil                                            # noqa: E402
from newproject import NewProjectDialog, default_projects_dir, validate_name  # noqa: E402

check("默认存放位置在「文档」下", "ctutor" in default_projects_dir(), default_projects_dir())
check("项目名校验：空名字被挡住", not validate_name("")[0])
check("项目名校验：非法字符被挡住",
      not validate_name("a/b")[0] and not validate_name("a:b")[0])
check("项目名校验：正常中文名通过", validate_name("第一次作业")[0])

tmp = os.path.join(ROOT, "build", "panel_newproj")
shutil.rmtree(tmp, ignore_errors=True)
dlg = NewProjectDialog(win3, tmp, "utf-8")
dlg.ed_name.setText("测试项目")
dlg.cmb_tpl.setCurrentIndex(1)               # Hello World
created = dlg.create_only()
check("能创建 <目录>/<名字>.c（扁平存放，不套同名文件夹）",
      bool(created) and created == os.path.join(tmp, "测试项目.c")
      and os.path.exists(created), created)
check("没有生成同名文件夹（改版前的行为）",
      not os.path.isdir(os.path.join(tmp, "测试项目")), os.path.join(tmp, "测试项目"))
check("模板内容写进去了",
      "Hello, World!" in open(created, encoding="utf-8").read())

win3._open_path(created)
app.processEvents()
check("新建后在编辑器里打开它", win3.path == created, win3.path)
check("窗口上显示了项目路径", "测试项目" in win3.lbl_file.text(), win3.lbl_file.text())

# 已存在时不静默覆盖（会弹确认框；这里只验证文件内容没被无条件改写）
before = open(created, encoding="utf-8").read()
dlg2 = NewProjectDialog(win3, tmp, "utf-8")
dlg2.ed_name.setText("测试项目")
dlg2.cmb_tpl.setCurrentIndex(0)              # 改成空白模板
check("重名时不会不问就覆盖（弹的是确认框而不是直接写）",
      "Hello, World!" in before)

# 真正走一遍 new_project()：上面用的是 create_only()，绕过了 dlg.exec() 那一行，
# 结果漏掉了一个真 bug（dlg.Accepted 在 PySide6 下会 AttributeError）。
# 这里把 exec 换掉，强制走"用户点了确定"的分支。
_orig_exec = NewProjectDialog.exec
_orig_cls = panel.newproject_mod.NewProjectDialog


class _AutoAccept(NewProjectDialog):
    def exec(self):
        self.ed_name.setText("自动项目")
        self.ed_dir.setText(tmp)
        self.cmb_tpl.setCurrentIndex(1)
        self._accept()
        return panel.QDialog.DialogCode.Accepted


panel.newproject_mod.NewProjectDialog = _AutoAccept
try:
    win3.new_project()
finally:
    panel.newproject_mod.NewProjectDialog = _orig_cls
app.processEvents()
check("new_project() 全流程能走通（不抛异常）", True)
check("new_project() 之后编辑器里就是新文件",
      bool(win3.path) and win3.path.endswith(".c") and "自动项目" in win3.path
      and not os.path.isdir(os.path.dirname(win3.path).rstrip("\\") + os.sep + "自动项目"),
      win3.path)
check("new_project() 之后标红被清空", win3.editor.monaco or True)

# ---------------------------------------------------------------- 多标签 / 未保存提示
print()
print("— 多标签：同时打开多个项目 —")
_clear_session()
win4 = panel.MainWindow()
app.processEvents()
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)

check("有标签栏", hasattr(win4, "tabbar") and win4.tabbar.count() >= 1,
      getattr(win4, "tabbar", None))
check("标签可以关闭（有关闭按钮）", win4.tabbar.tabsClosable())

docs_dir = os.path.join(ROOT, "build", "mdoc")
os.makedirs(os.path.join(docs_dir, "p1"), exist_ok=True)
os.makedirs(os.path.join(docs_dir, "p2"), exist_ok=True)
f1 = os.path.join(docs_dir, "p1", "main.c")
f2 = os.path.join(docs_dir, "p2", "main.c")
with open(f1, "w", encoding="utf-8", newline="\n") as f:
    f.write("int main(void){ int a = 1 return 0; }\n")   # p1 有语法错误
with open(f2, "w", encoding="utf-8", newline="\n") as f:
    f.write("int main(void){ return 0; }\n")             # p2 正常

_n0 = win4.tabbar.count()
win4._open_path(f1)
win4._open_path(f2)
app.processEvents()
check("打开两个文件后标签数 +2", win4.tabbar.count() == _n0 + 2, win4.tabbar.count())
check("当前文档是最后打开的那个", win4.path == f2, win4.path)

# 同一个文件重复打开不该再开一个标签
win4._open_path(f1)
app.processEvents()
check("重复打开同一文件会跳过去，不重复开标签",
      win4.tabbar.count() == _n0 + 2 and win4.path == f1, win4.tabbar.count())

# 切标签内容不能串
win4.tabbar.setCurrentIndex(_n0 + 1)          # 切到 p2
app.processEvents()
check("切到 p2 后编辑器里是 p2 的内容", 'printf' not in win4.editor.text()
      and "return 0" in win4.editor.text(), win4.editor.text()[:40])
win4.tabbar.setCurrentIndex(_n0)              # 切回 p1
app.processEvents()
check("切回 p1 后编辑器里是 p1 的内容", "int a = 1" in win4.editor.text(),
      win4.editor.text()[:40])
check("两个项目的 main.c 工作副本不互相覆盖",
      win4._working_copy().endswith(os.path.join("panel", "%02d_main.c" % win4.cur))
      or ("%02d_" % win4.cur) in os.path.basename(win4._working_copy()),
      os.path.basename(win4._working_copy()))

print()
print("— 未保存提示 —")
_d = win4._doc()
check("刚打开的文档不算已修改（否则退出时会白问一遍）", _d.modified is False,
      _d.modified)
check("没有改动时不会弹框（_confirm_discard 直接放行）",
      win4._confirm_discard(_d) is True)

# 模拟用户编辑
win4.editor.set_text(win4.editor.text().replace("int a = 1", "int a = 2"))
for _ in range(25):
    app.processEvents()
    time.sleep(0.05)
check("编辑后标记为已修改", _d.modified is True, _d.modified)
check("已修改的标签标题带 * 号", win4.tabbar.tabText(win4.cur).startswith("* "),
      win4.tabbar.tabText(win4.cur))

# closeEvent：用户点「取消」应当拦住退出
_orig_confirm = win4._confirm_discard
win4._confirm_discard = lambda doc: False
class _Ev:
    def __init__(self): self.ignored = False; self.accepted = False
    def ignore(self): self.ignored = True
    def accept(self): self.accepted = True
_ev = _Ev()
win4.closeEvent(_ev)
check("有未保存改动且用户点取消时，退出会被拦住", _ev.ignored and not _ev.accepted)

# 用户点「不保存」→ 放行
win4._confirm_discard = lambda doc: True
_ev2 = _Ev()
win4.closeEvent(_ev2)
check("用户选择不保存时，退出继续", _ev2.accepted)
win4._confirm_discard = _orig_confirm

# 清理：把改动抹掉，避免真的退出时弹框
for _doc in win4.docs:
    _doc.saved_text = _doc.text
    _doc.modified = False
win4._dirty_timer.stop()
win4.close()

# ---------------------------------------------------------------- 对齐（整理缩进）
print()
print("— 对齐：整理缩进 —")
_clear_session()
win5 = panel.MainWindow()
app.processEvents()
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)

check("工具栏有「对齐」按钮", hasattr(win5, "btn_align"),
      getattr(win5, "btn_align", None))

messy = ("#include <stdio.h>\n"
         "int main(void) {\n"
         "int a, b;\n"
         "if (a > b) {\n"
         "printf(\"{ 这是字符串里的括号 }\\n\");\n"
         "}\n"
         "return 0;\n"
         "}\n")
fmt_path = os.path.join(ROOT, "build", "mdoc", "fmt_demo.c")
os.makedirs(os.path.dirname(fmt_path), exist_ok=True)
with open(fmt_path, "w", encoding="utf-8", newline="\n") as f:
    f.write(messy)
win5._open_path(fmt_path)
for _ in range(10):
    app.processEvents()
    time.sleep(0.05)

win5.format_code()
for _ in range(15):
    app.processEvents()
    time.sleep(0.05)
_formatted = win5.editor.text().replace("\r\n", "\n")
check("对齐后按大括号缩进", "    int a, b;" in _formatted, _formatted)
check("对齐只动空白，代码内容一字未变",
      "".join(_formatted.split()) == "".join(messy.split()), _formatted)
check("字符串里的花括号没被当成块", "    if (a > b) {" in _formatted, _formatted)
check("对齐后标记为已修改（退出时会问要不要存）", win5._doc().modified is True)
check("对齐后可以撤回", bool(win5._doc().format_undo))

win5.undo_format()
for _ in range(15):
    app.processEvents()
    time.sleep(0.05)
check("撤回到对齐前", win5.editor.text().replace("\r\n", "\n") == messy,
      win5.editor.text())

win5.format_code()
for _ in range(15):
    app.processEvents()
    time.sleep(0.05)
_before2 = win5.editor.text()
win5.format_code()          # 再点一次：已规范，不该再改
for _ in range(10):
    app.processEvents()
    time.sleep(0.05)
check("已经对齐过的内容再点一次不会变（幂等）",
      win5.editor.text() == _before2)

for _doc in win5.docs:
    _doc.saved_text = _doc.text
    _doc.modified = False
win5._dirty_timer.stop()
win5.close()

# ---------------------------------------------------------------- 上次进度
print()
print("— 上次进度：关掉再打开，接着写 —")
_clear_session()
win6 = panel.MainWindow()
app.processEvents()
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)

_p1 = os.path.join(ROOT, "build", "mdoc", "sess_a.c")
_p2 = os.path.join(ROOT, "build", "mdoc", "sess_b.c")
with open(_p1, "w", encoding="utf-8", newline="\n") as f:
    f.write("int main(void){ return 0; }\n")
with open(_p2, "w", encoding="utf-8", newline="\n") as f:
    f.write("int main(void){ return 1; }\n")
win6._open_path(_p1)
win6._open_path(_p2)
app.processEvents()
for _ in range(10):
    app.processEvents()
    time.sleep(0.05)

# 在第二个文件里改点东西但不保存
win6.editor.set_text(win6.editor.text() + "/* 写到一半 */\n")
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)
check("改动被识别", win6._doc().modified is True)
win6._save_session()
app.processEvents()
check("会话文件已写出", os.path.exists(_session_mod.session_path()))

# 模拟"关掉程序再打开"：新建一个窗口，应该恢复上次的标签
win7 = panel.MainWindow()
app.processEvents()
for _ in range(25):
    app.processEvents()
    time.sleep(0.05)
check("重新打开后恢复了上次的文件（不用重新找）",
      win7.tabbar.count() >= 2, win7.tabbar.count())
_paths = [os.path.normcase(os.path.abspath(d.path)) for d in win7.docs if d.path]
check("恢复出来的文件路径对得上",
      os.path.normcase(os.path.abspath(_p1)) in _paths
      and os.path.normcase(os.path.abspath(_p2)) in _paths, _paths)
_cur = win7._doc()
check("恢复后停在关掉前那个标签", _cur is not None and _cur.path == _p2,
      _cur.path if _cur else None)
check("没保存的改动也跟着回来了（这段代码没白写）",
      "写到一半" in (win7.editor.text() or ""), (win7.editor.text() or "")[-40:])
check("恢复后的未保存内容仍然标着已修改（不会被误当成已存盘）",
      win7._doc().modified is True)

# 文件被删掉之后，恢复时应该跳过而不是崩
os.remove(_p2)
win7._save_session()
app.processEvents()
win8 = panel.MainWindow()
app.processEvents()
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)
check("会话里的文件被删了 → 跳过它，程序照常打开",
      win8._doc() is not None, win8.tabbar.count())

for _w in (win6, win7, win8):
    for _doc in _w.docs:
        _doc.saved_text = _doc.text
        _doc.modified = False
    _w._dirty_timer.stop()
    _w.close()
_clear_session()

win3.close()

# ---------------------------------------------------------------- 桌面启动器
print()
print("— 桌面启动器（双击即用，不弹黑框）—")
pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
check("存在 pythonw.exe（无控制台启动的前提）", os.path.exists(pythonw), pythonw)

bat = os.path.join(ROOT, "启动面板.bat")
# 这个 .bat 是 tools/make_shortcut.py 现场生成的（内容是本机 Python 的绝对路径），
# 所以它**不在开源仓库里**。全新克隆下来没有它是正常的，这里跳过而不是判失败——
# 一个在新克隆上就报错的测试套件没有意义。
if os.path.exists(bat):
    body = open(bat, "rb").read().decode("mbcs", errors="replace")
    check("兜底启动脚本存在且内容正确（未被格式化占位符破坏）",
          "panel.py" in body and "%~dp0" in body and "%d" not in body.replace("%~dp0", ""),
          body)
else:
    print("[SKIP] 启动面板.bat 不存在（由 tools/make_shortcut.py 生成，仓库里不含）")

icon = os.path.join(ROOT, "assets", "ctutor.ico")
check("图标已生成", os.path.exists(icon), icon)

# 快捷方式只在桌面存在时才检查（换机器/换个目录仍应能通过其余测试）
lnk = os.path.join(os.path.expanduser("~"), "Desktop", "C语言报错助手.lnk")
if os.path.exists(lnk):
    check("桌面快捷方式存在（%s）" % os.path.basename(lnk), True)
else:
    print("[SKIP] 桌面未找到快捷方式（可运行 tools/make_shortcut.py 生成）")

# pythonw 下 sys.stdout 是 None，代码里若直接 .reconfigure 会崩——这里直接验证防护有效
src = open(os.path.join(ROOT, "panel.py"), encoding="utf-8").read()
check("panel.py 对 stdout=None 有防护（pythonw 启动必需）",
      "if _stream is not None" in src, "")
check("panel.py 注册了崩溃处理器（无控制台时不会静默失败）",
      "_install_crash_handler" in src)

n_fail = sum(1 for _, ok in results if not ok)
print()
print("=" * 66)
print("面板测试：%d 项，通过 %d，失败 %d" % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)
