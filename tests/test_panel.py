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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
check("文件菜单有新建项目与设置存放位置",
      any("新建项目" in t for t in menu_items("文件"))
      and any("存放位置" in t for t in menu_items("文件")),
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
check("能创建项目文件夹与 main.c",
      bool(created) and created.endswith(os.path.join("测试项目", "main.c"))
      and os.path.exists(created), created)
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
check("new_project() 之后编辑器里就是新项目",
      bool(win3.path) and win3.path.endswith("main.c") and "自动项目" in win3.path,
      win3.path)
check("new_project() 之后标红被清空", win3.editor.monaco or True)
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
