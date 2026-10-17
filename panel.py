#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ctutor 图形面板（PySide6 + Monaco 编辑器）。

设计要点：
  - **绝不修改学生原文件**：检查时把编辑区内容写到 build/panel/ 的工作副本，
    编译的是副本；只有显式按「保存」才写回原文件。
  - **界面与命令行共用同一条引擎路径**（engine.report.check_once），
    因此两个界面的结论不会分裂。
  - **Monaco 不可用时自动退回普通编辑器**，面板仍然可用（qtmonaco 官方只声明
    支持 macOS/Linux，Windows 属未验证路径，必须有降级方案）。
  - 源文件编码可选（GBK / UTF-8），状态栏显示当前编码——中文课堂必须让学生看得见。
"""
import argparse
import html
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pythonw.exe（无控制台启动）下 sys.stdout / sys.stderr 是 None，
# 直接 .reconfigure 会抛 AttributeError 让面板根本起不来——桌面快捷方式正是这种启动方式。
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None:
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

from PySide6.QtCore import Qt, QEvent, QThread, QTimer, Signal            # noqa: E402
from PySide6.QtGui import QAction, QFont, QFontDatabase, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,   # noqa: E402
                               QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)
from engine import ai as ai_mod                                     # noqa: E402
from engine import completion as completion_mod                     # noqa: E402
from engine import config as config_mod                             # noqa: E402
from engine import compilers as compilers_mod                       # noqa: E402
from engine import editorui as editorui_mod                         # noqa: E402
from engine import report as report_mod                             # noqa: E402
import newproject as newproject_mod                                 # noqa: E402
from engine.encoding import detect_source_encoding, encoding_label, read_source  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ENCODINGS = [("GBK（中文 Windows 默认，Dev-C++ 同款）", "mbcs"),
             ("UTF-8", "utf-8"),
             ("UTF-8 带 BOM（VS 推荐）", "utf-8-sig")]

SAMPLE = '''#include <stdio.h>

int main(void) {
    int x = 10；
    printf("%d\\n", x);
    return 0;
}
'''


# ---------------------------------------------------------------- 中文字体
def ensure_cjk_font(app) -> str:
    """显式加载 Windows 自带的中文字体，返回字族名（失败返回空串）。

    为什么必须显式加载：Qt 在找不到字族时会**静默把所有中文画成方块**。
    实测离屏渲染时 QFontDatabase.families() 返回 0 个字体，整个界面全是 □；
    部分受限环境（精简系统、无字体配置）也可能出现同类问题。
    显式加载一个已知存在的中文字体文件最稳妥。
    """
    for p in (r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
              r"C:\Windows\Fonts\msyh.ttf",
              r"C:\Windows\Fonts\simhei.ttf",    # 黑体
              r"C:\Windows\Fonts\simsun.ttc"):   # 宋体
        if not os.path.exists(p):
            continue
        fid = QFontDatabase.addApplicationFont(p)
        if fid == -1:
            continue
        fams = QFontDatabase.applicationFontFamilies(fid)
        if fams:
            app.setFont(QFont(fams[0], 10))
            return fams[0]
    return ""


# ---------------------------------------------------------------- 编辑器适配
class EditorAdapter:
    """优先使用 Monaco（并注入 C 语言补全）；不可用时退回 QPlainTextEdit。"""

    def __init__(self):
        self.monaco = False
        self.note = ""
        self.completion_info = {}
        try:
            # 拦掉 qtmonaco 的 Python LSP 启动。
            # 它在构造 Monaco 时会执行 sys.executable -m pylsp —— 我们编辑的是 C，
            # 用不上 Python 语言服务；而且**打包后 sys.executable 就是本程序自己**，
            # 会变成"启动自己"，属于典型的打包翻车点。
            import qtmonaco.monaco as _qm
            _qm.get_pylsp_host = lambda: ""

            from qtmonaco import Monaco
            from qtmonaco.resource_loader import get_monaco_base_url, get_monaco_html
            # 两段注入：代码补全 + 出错位置标红。
            # 各自 import 同一份 monaco-core.js —— ES 模块有缓存，拿到同一个实例。
            script = completion_mod.build_script() + editorui_mod.build_script()
            self.script = script

            class CtutorMonaco(Monaco):
                """覆盖 _load_editor 注入脚本——不改动 qtmonaco 包本身，升级依赖不受影响。"""

                def _load_editor(self):
                    raw = get_monaco_html()
                    if "</body>" in raw:
                        raw = raw.replace("</body>", script + "</body>")
                    else:
                        raw += script
                    self.setHtml(raw, get_monaco_base_url())

            self.widget = CtutorMonaco()
            self.widget.set_language("c")
            self.widget.set_theme("vs-dark")
            self.widget.set_minimap_enabled(False)
            self.monaco = True
            self.note = "编辑器：Monaco（VS Code 同款）"
        except Exception as e:                                      # noqa: BLE001
            self.widget = QPlainTextEdit()
            self.widget.setFont(QFont("Consolas", 11))
            self.widget.setPlaceholderText("在此输入 C 代码…")
            self.note = "编辑器：普通文本（Monaco 不可用：%s）" % type(e).__name__

    def probe_completion(self, callback, tries: int = 12) -> None:
        """查询补全提供者是否注册成功；JS 侧是异步的，所以轮询几次。"""
        if not self.monaco:
            callback({"stage": "unavailable"})
            return

        def once(left):
            def got(v):
                try:
                    info = json.loads(v) if v else {"stage": "empty"}
                except ValueError:
                    info = {"stage": "bad-json"}
                if info.get("stage") in ("done", "error") or left <= 1:
                    self.completion_info = info
                    callback(info)
                else:
                    QTimer.singleShot(400, lambda: once(left - 1))
            try:
                self.widget.page().runJavaScript(
                    "JSON.stringify(window.__ctutor_comp || {stage:'pending'})", got)
            except Exception as e:                                  # noqa: BLE001
                callback({"stage": "error", "error": str(e)})

        once(tries)

    def text(self) -> str:
        try:
            return self.widget.get_text() if self.monaco else self.widget.toPlainText()
        except Exception:                                           # noqa: BLE001
            return ""

    def set_text(self, s: str) -> None:
        try:
            if self.monaco:
                self.widget.set_text(s)
            else:
                self.widget.setPlainText(s)
        except Exception:                                           # noqa: BLE001
            pass

    def goto(self, line: int, column: int) -> None:
        try:
            if self.monaco and line:
                self.widget.set_cursor(int(line), max(1, int(column)))
        except Exception:                                           # noqa: BLE001
            pass

    # ---- 出错位置标红 ----
    def mark_errors(self, items, callback=None) -> None:
        """把诊断标到编辑器里。items 由 editorui.items_from_diagnostics 生成。"""
        if not self.monaco:
            self._mark_errors_plain(items)
            return
        payload = json.dumps(items, ensure_ascii=False)
        js = "window.__ctutor_mark(%s)" % payload
        # PySide6 的 runJavaScript 有两个重载；显式传 None 当回调会出问题，所以分两种调法
        if callback is None:
            self.widget.page().runJavaScript(js)
        else:
            self.widget.page().runJavaScript(js, callback)

    def _mark_errors_plain(self, items) -> None:
        """降级编辑器（Monaco 加载失败时）也要能标红，否则功能在那种机器上直接没了。"""
        try:
            from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
            from PySide6.QtWidgets import QTextEdit

            doc = self.widget.document()
            sels = []
            for it in items or []:
                line = int(it.get("line") or 0)
                if line <= 0:
                    continue
                block = doc.findBlockByNumber(line - 1)
                if not block.isValid():
                    continue
                fmt = QTextCharFormat()
                if it.get("primary"):
                    fmt.setBackground(QColor(220, 38, 38, 70))
                    fmt.setUnderlineColor(QColor(220, 38, 38))
                    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                else:
                    fmt.setBackground(QColor(220, 38, 38, 28))
                sel = QTextEdit.ExtraSelection()
                sel.format = fmt
                sel.cursor = QTextCursor(block)
                sels.append(sel)
            self.widget.setExtraSelections(sels)
        except Exception:                                           # noqa: BLE001
            pass

    def clear_marks(self) -> None:
        if not self.monaco:
            self._mark_errors_plain([])
            return
        try:
            self.widget.page().runJavaScript("window.__ctutor_clear_marks()")
        except Exception:                                           # noqa: BLE001
            pass

    def reveal_line(self, line: int) -> None:
        """滚动到出错行（不抢焦点，避免打断正在打字的人）。"""
        if not self.monaco or not line:
            return
        try:
            self.widget.page().runJavaScript("window.__ctutor_reveal(%d)" % int(line))
        except Exception:                                           # noqa: BLE001
            pass

    def probe_marks(self, callback) -> None:
        """查询标红是否生效（供测试/自检用）。"""
        if not self.monaco:
            callback({"stage": "unavailable"})
            return
        try:
            self.widget.page().runJavaScript(
                "JSON.stringify(window.__ctutor_mark_state || {stage:'pending'})",
                lambda v: callback(json.loads(v) if v else {"stage": "empty"}))
        except Exception as e:                                      # noqa: BLE001
            callback({"stage": "error", "error": str(e)})

    def set_zoom(self, step: int, callback=None) -> None:
        """调整编辑器字号。step 是相对默认值的档数（1 档 = 2px）。"""
        if not self.monaco:
            # 降级编辑器：直接改字体点数
            try:
                self.widget.setFont(QFont("Consolas", max(7, 11 + int(step))))
            except Exception:                                       # noqa: BLE001
                pass
            return
        js = "window.__ctutor_zoom(%d)" % int(step)
        if callback is None:
            self.widget.page().runJavaScript(js)
        else:
            self.widget.page().runJavaScript(js, callback)

    def probe_zoom(self, callback) -> None:
        if not self.monaco:
            callback({"stage": "unavailable"})
            return
        try:
            self.widget.page().runJavaScript(
                "String(window.__ctutor_font_size || '')",
                lambda v: callback({"stage": "ok", "size": v}))
        except Exception as e:                                      # noqa: BLE001
            callback({"stage": "error", "error": str(e)})


# ---------------------------------------------------------------- 后台检查
class CheckWorker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, working_path, depth, use_ai, use_ai_always, prefer, parent=None):
        super().__init__(parent)
        self.working_path = working_path
        self.depth = depth
        self.use_ai = use_ai
        self.use_ai_always = use_ai_always
        self.prefer = prefer

    def run(self):
        try:
            wd = os.path.join(config_mod.path_of("build"), "panel_run")
            rep = report_mod.check_once(
                self.working_path, depth=self.depth, use_ai=self.use_ai,
                ai_always=self.use_ai_always, compiler_prefer=self.prefer, workdir=wd)
            self.done.emit(rep)
        except Exception:                                           # noqa: BLE001
            self.failed.emit(traceback.format_exc())


class RunWorker(QThread):
    """在后台编译并运行程序。

    两种模式：
      · "console" —— 开一个真正的系统命令行窗口，scanf 可以交互输入（默认）；
      · "panel"   —— 捕获输出显示在面板里（适合不需要交互、想直接看结果的场合）。

    必须放后台：面板模式可能有死循环（就算有超时，卡在主线程里界面也会冻住）；
    两种模式都要先编译，编译本身也要几百毫秒到几秒。
    """

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, working_path, stdin_text, source_encoding, prefer,
                 timeout=10, mode="console", parent=None):
        super().__init__(parent)
        self.working_path = working_path
        self.stdin_text = stdin_text
        self.source_encoding = source_encoding
        self.prefer = prefer
        self.timeout = timeout
        self.mode = mode

    def run(self):
        try:
            from engine import run as run_mod

            # 每次运行用独立目录：上一次的控制台窗口可能还开着、锁着 a.exe，
            # 共用目录会让下一次编译直接失败，报成"编译没有通过"，极难排查。
            wd = run_mod.unique_workdir(
                os.path.join(config_mod.path_of("build"), "panel_run"))
            comp = compilers_mod.detect(self.prefer, config_mod.load())
            if self.mode == "console":
                res = run_mod.run_in_console(self.working_path, wd, compiler=comp)
            else:
                res = run_mod.run_once(self.working_path, wd,
                                       stdin_text=self.stdin_text,
                                       timeout=self.timeout,
                                       source_encoding=self.source_encoding,
                                       compiler=comp)
            self.done.emit(res)
        except Exception:                                           # noqa: BLE001
            self.failed.emit(traceback.format_exc())


class AITestWorker(QThread):
    """在后台线程测 DeepSeek 连通性——网络请求会阻塞界面，不能直接在主线程里调。"""

    done = Signal(bool, str)

    def run(self):
        try:
            client = ai_mod.DeepSeekClient()
            text, usage = client.chat([{"role": "user", "content": "请只回答两个字：正常"}],
                                      json_mode=False, max_tokens=200)
            self.done.emit(True, "连接正常　模型 %s　返回 %r\n密钥来源：%s"
                           % (client.model, text.strip()[:20], ai_mod.key_source()))
        except ai_mod.AIError as e:
            self.done.emit(False, str(e))
        except Exception as e:                                  # noqa: BLE001
            self.done.emit(False, repr(e))


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 字体在窗口构造时就加载，而不是只在 main() 里——
        # 否则通过其他入口（测试、探针、被别的程序 import）构造窗口时中文会变成方块。
        _app = QApplication.instance()
        if _app is not None and not getattr(_app, "_ctutor_font_ready", False):
            ensure_cjk_font(_app)
            _app._ctutor_font_ready = True
        self.setWindowTitle("ctutor — C 语言报错解释器")
        self.resize(1360, 880)
        self.path = None
        self.encoding = "mbcs"
        self.editor = EditorAdapter()
        self.worker = None
        self.run_worker = None
        self._explanation_html = ""          # 最近一次的讲解（供「帮助」关闭后恢复）
        self._zoom = 0                       # 相对默认字号的缩放步数
        self._build_ui()
        self._load_sample()
        # 缩放的事件过滤器挂到应用级别：Monaco 内嵌 Chromium 会吞掉快捷键，
        # 挂在这里才能在事件送达焦点控件之前拦住（也才能被自动化测试验证）。
        _app = QApplication.instance()
        if _app is not None:
            _app.installEventFilter(self)
        # 恢复上次的缩放档位（等 Monaco 加载完再设编辑器字号，否则会被覆盖）
        try:
            z = int((config_mod.load().get("ui") or {}).get("zoom", 0) or 0)
        except Exception:                                       # noqa: BLE001
            z = 0
        if z:
            self._zoom = z
            QTimer.singleShot(1200, self._apply_zoom)
        # 补全脚本在页面加载后异步注册，稍等一下再查询状态
        QTimer.singleShot(1500, self._check_completion_status)

    def _check_completion_status(self):
        def done(info):
            if info.get("registered"):
                self.status.showMessage(
                    "补全已启用：%d 条 C 语言词条　输入前几个字母就会弹出候选（Tab 或回车选中）"
                    % info.get("count", 0), 10000)
            elif info.get("stage") == "error":
                self.status.showMessage("补全未启用：%s（编辑与检查不受影响）"
                                        % info.get("error", ""), 10000)
            else:
                self.status.showMessage(self.editor.note, 5000)
        self.editor.probe_completion(done)

    # ---------- 界面 ----------
    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        # 工具栏只留"每次都要点"的按钮。
        # 打开/帮助等命令只保留菜单一个入口 —— 之前两处都有，是重复的。
        self.btn_new = QPushButton("新建项目")
        self.btn_new.setToolTip("新建项目文件夹并设置存放位置（Ctrl+N）")
        self.btn_save = QPushButton("保存")
        self.btn_save.setToolTip("保存当前文件（Ctrl+S）")
        self.btn_check = QPushButton("检查 (F5)")
        self.btn_check.setDefault(True)
        self.btn_run = QPushButton("运行 (F9)")
        self.btn_run.setToolTip(
            "在**系统命令行窗口**里运行，scanf 可以真正交互输入。\n"
            "想直接在面板里看输出（不弹窗），用 Shift+F9。")
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(1, 60)
        self.spin_timeout.setValue(10)
        self.spin_timeout.setSuffix(" 秒")
        self.spin_timeout.setToolTip("面板内运行的最长时间，超过就强制终止（防止死循环卡住）")
        self.cmb_depth = QComboBox()
        for i in range(1, 6):
            self.cmb_depth.addItem("讲到第 %d 层" % i, i)
        self.cmb_depth.setCurrentIndex(3)                    # 默认 4 层
        self.chk_ai = QCheckBox("AI 补充")
        self.chk_ai.setToolTip("仅在本地知识库未收录时调用 DeepSeek；命中则不调用，零成本")
        # 勾选状态持久化：先按配置设好再连信号，避免启动时误弹「设置密钥」对话框
        try:
            self.chk_ai.setChecked(bool((config_mod.load().get("ai") or {}).get("enabled")))
        except Exception:                                       # noqa: BLE001
            pass
        self.chk_ai.toggled.connect(self._ai_toggled)
        self.cmb_enc = QComboBox()
        for label, code in ENCODINGS:
            self.cmb_enc.addItem(label, code)

        bar.addWidget(self.btn_new)
        bar.addWidget(self.btn_save)
        bar.addSpacing(12)
        bar.addWidget(self.btn_check)
        bar.addWidget(self.btn_run)
        bar.addWidget(self.spin_timeout)
        bar.addStretch(1)
        bar.addWidget(self.cmb_depth)
        bar.addWidget(self.chk_ai)
        bar.addSpacing(12)
        bar.addWidget(QLabel("编码："))
        bar.addWidget(self.cmb_enc)
        layout.addLayout(bar)

        self.lbl_file = QLabel("未命名")
        self.lbl_file.setStyleSheet("color:#4B5563;")
        layout.addWidget(self.lbl_file)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.editor.widget)

        # 下方面板分两个页签：讲解 / 运行输出。
        # 用页签而不是共用一个区域，是因为两者都需要保留 —— 看完运行结果还想回头看讲解。
        self.tabs = QTabWidget()
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet("QTextBrowser{background:#FFFFFF;border:1px solid #dde3ea;}")
        self.tabs.addTab(self.view, "讲解")

        run_page = QWidget()
        rl = QVBoxLayout(run_page)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        inrow = QHBoxLayout()
        inrow.addWidget(QLabel("程序输入："))
        self.txt_input = QPlainTextEdit()
        self.txt_input.setPlaceholderText(
            "程序用到 scanf / gets 时，把要输入的内容填在这里（每行一个，按运行时的顺序）")
        self.txt_input.setFixedHeight(52)
        self.txt_input.setFont(QFont("Consolas", 10))
        inrow.addWidget(self.txt_input, 1)
        rl.addLayout(inrow)
        self.view_run = QTextBrowser()
        self.view_run.setOpenExternalLinks(True)
        self.view_run.setStyleSheet(
            "QTextBrowser{background:#FFFFFF;border:1px solid #dde3ea;}")
        rl.addWidget(self.view_run, 1)
        self.tabs.addTab(run_page, "运行输出")
        # 缩放的事件过滤器挂在 QApplication 上（见 __init__），
        # 这样无论焦点在编辑器还是输出区都能生效。
        splitter.addWidget(self.tabs)
        splitter.setSizes([460, 380])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.status = self.statusBar()
        self.status.showMessage(self.editor.note)

        act_new = QAction("新建项目…", self)
        act_new.setShortcut("Ctrl+N")
        act_new.triggered.connect(self.new_project)
        act_open = QAction("打开…", self)
        # 不要写 QKeySequence.Open！那是 StandardKey 枚举值（16），
        # setShortcut 会把它当成"按键码 16"，注册出一个无意义的控制字符，
        # 快捷键**静默失效**（QKeySequence(16) == '\x10'）。
        # 也不能用 QKeySequence.keyBindings(StandardKey.Open) —— PySide6 这个构建里它会直接崩。
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self.open_file)
        act_save = QAction("保存", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self.save_file)
        act_save_as = QAction("另存为…", self)
        act_save_as.setShortcut("Ctrl+Shift+S")
        act_save_as.triggered.connect(lambda: self.save_file(force_dialog=True))
        act_where = QAction("设置项目存放位置…", self)
        act_where.triggered.connect(self.set_projects_dir)
        act_check = QAction("检查", self)
        act_check.setShortcut("F5")
        act_check.triggered.connect(self.do_check)
        act_run = QAction("在系统命令行中运行", self)
        act_run.setShortcut("F9")
        act_run.triggered.connect(self.do_run)
        act_run_panel = QAction("在面板内运行（输出显示在下方）", self)
        act_run_panel.setShortcut("Shift+F9")
        act_run_panel.triggered.connect(self.do_run_panel)
        self.btn_run.clicked.connect(self.do_run)
        self.btn_new.clicked.connect(self.new_project)
        self.btn_save.clicked.connect(self.save_file)
        self.btn_check.clicked.connect(self.do_check)
        # Ctrl+加号/减号/0 缩放：学生看代码看累了想放大，是很自然的诉求。
        #
        # 键盘上"加号"有很多种按法，所以一次性注册多个：
        #   Ctrl+=  —— 不按 Shift 直接按等号键（很多人的习惯）
        #   Ctrl++  —— 小键盘的加号 / 按了 Shift 的等号
        #   Ctrl+Shift+=  —— 显式写出来，避免某些键盘布局下解析不一致
        # 全部用 Qt.ApplicationShortcut：Monaco 是内嵌网页，
        # 默认的 WindowShortcut 有可能被它吞掉。
        act_zoom_in = QAction("放大", self)
        act_zoom_in.setShortcuts(["Ctrl+=", "Ctrl++", "Ctrl+Shift+="])
        act_zoom_in.setShortcutContext(Qt.ApplicationShortcut)
        act_zoom_in.triggered.connect(lambda: self.zoom_by(1))
        act_zoom_out = QAction("缩小", self)
        act_zoom_out.setShortcuts(["Ctrl+-", "Ctrl+Shift+-"])
        act_zoom_out.setShortcutContext(Qt.ApplicationShortcut)
        act_zoom_out.triggered.connect(lambda: self.zoom_by(-1))
        act_zoom_reset = QAction("恢复默认字号", self)
        act_zoom_reset.setShortcut("Ctrl+0")
        act_zoom_reset.setShortcutContext(Qt.ApplicationShortcut)
        act_zoom_reset.triggered.connect(self.zoom_reset)
        self.addAction(act_zoom_in)
        self.addAction(act_zoom_out)
        self.addAction(act_zoom_reset)

        m = self.menuBar().addMenu("文件")
        m.addAction(act_new)
        m.addAction(act_open)
        m.addSeparator()
        m.addAction(act_save)
        m.addAction(act_save_as)
        m.addSeparator()
        m.addAction(act_where)
        m.addAction("载入示例", self._load_sample)
        m.addSeparator()
        m.addAction("退出", self.close)

        # 「检查」菜单改名「运行」：它本来就同时管检查与运行，而且缩放不该塞在这里
        m2 = self.menuBar().addMenu("运行")
        m2.addAction(act_check)
        m2.addAction(act_run)
        m2.addAction(act_run_panel)

        m_view = self.menuBar().addMenu("视图")
        m_view.addAction(act_zoom_in)
        m_view.addAction(act_zoom_out)
        m_view.addAction(act_zoom_reset)

        m3 = self.menuBar().addMenu("帮助")
        # 唯一的「使用说明」入口（工具栏那个重复按钮已删除），F1 仍可用
        self.act_help = QAction("使用说明", self)
        self.act_help.setCheckable(True)
        self.act_help.setChecked(True)
        self.act_help.setShortcut("F1")
        self.act_help.toggled.connect(self._apply_view)
        m3.addAction(self.act_help)

        m4 = self.menuBar().addMenu("AI")
        self.act_ai_key = QAction("设置 DeepSeek 密钥…", self)
        self.act_ai_key.triggered.connect(self.set_api_key)
        m4.addAction(self.act_ai_key)
        self.act_ai_test = QAction("测试连接", self)
        self.act_ai_test.triggered.connect(self.test_ai)
        m4.addAction(self.act_ai_test)
        m4.addSeparator()
        self.act_ai_info = QAction("", self)
        self.act_ai_info.setEnabled(False)
        m4.addAction(self.act_ai_info)
        self._refresh_ai_menu()

        # 按钮的信号连接统一放在上面的 QAction 那段，避免同一按钮被连两次
        self.cmb_depth.currentIndexChanged.connect(self._rerender_current)
        self.cmb_enc.currentIndexChanged.connect(self._encoding_changed)

        self._explanation_html = self._empty_html()
        self._apply_view()

    # ---------- 下方面板：说明 / 讲解 二选一 ----------
    def _help_html(self) -> str:
        return (
            '<div style="font-family:\'Microsoft YaHei\',sans-serif;font-size:11pt;'
            'color:#374151;line-height:1.8;">'
            '<p><b>这是一个「帮你看懂编译报错」的工具，它不会替你改代码。</b></p>'
            '<p>用法：把 C 代码贴进上面的编辑区，按 <b>F5</b> 或点「检查」。</p>'
            '<ul>'
            '<li><b>输入前几个字母就会有候选</b>——例如打 <code>pri</code> 选 printf，'
            '打 <code>for</code> 直接补出完整循环，按 Tab 或回车选中，减少打字与拼写错误</li>'
            '<li>只讲<b>最可能相关的一条</b>错误，避免几十条报错一次糊上来</li>'
            '<li>解释分 5 层：结论 → 定位 → 原因 → 改法 → 知识点</li>'
            '<li>本地知识库优先，命中时<b>不联网、不花钱</b></li>'
            '<li>行号与位置<b>只来自编译器</b>，不由模型编造</li>'
            '</ul>'
            '<p><b>运行怎么用：</b>按 <b>F9</b> 会开一个系统命令行窗口来运行程序，'
            '这样 <code>scanf</code> 可以真正交互输入；'
            '想不弹窗、直接在下面看输出，用 <b>Shift+F9</b>。</p>'
            '<p><b>字号太小看不清？</b>按 <b>Ctrl+=</b> 放大、<b>Ctrl+-</b> 缩小、'
            '<b>Ctrl+0</b> 恢复默认；也可以按住 <b>Ctrl</b> 滚滚轮。字号会记住。</p>'
            '<p style="color:#6B7280;font-size:10pt;">你的原文件不会被修改；'
            '检查和运行用的都是工作副本。<br>'
            '看完按 <b>F1</b> 收起说明，把位置让给讲解。</p></div>')

    def _empty_html(self) -> str:
        return (
            '<div style="font-family:\'Microsoft YaHei\',sans-serif;font-size:11pt;'
            'color:#6B7280;padding:16px;line-height:1.9;">'
            '按 <b>F5</b> 或点工具栏的「检查」，讲解会显示在这里。<br>'
            '按 <b>F9</b> 会在系统命令行窗口里运行程序（scanf 可以交互输入）。<br>'
            '<span style="font-size:10pt;">想再看一遍使用说明：按 <b>F1</b>，'
            '或菜单「帮助 → 使用说明」。</span>'
            '</div>')

    def _apply_view(self):
        """按「使用说明」的开关状态，决定下方面板显示说明还是讲解。

        原来同时存在一个工具栏「帮助」按钮和菜单项，两边还要互相同步状态——
        纯粹的重复。现在只留菜单项一个入口。
        """
        show_help = self.act_help.isChecked()
        self.view.setHtml(self._help_html() if show_help else self._explanation_html)

    # ---------- AI ----------
    def _refresh_ai_menu(self):
        src = ai_mod.key_source()
        if src:
            self.act_ai_info.setText("已配置密钥：%s" % src)
            self.chk_ai.setToolTip("仅在本地知识库未收录时调用 DeepSeek。密钥来源：%s" % src)
        else:
            self.act_ai_info.setText("未配置密钥（AI 补充不可用；本地解释不受影响）")
            self.chk_ai.setToolTip("点菜单 AI → 设置 DeepSeek 密钥 后可用。"
                                   "本地知识库命中时不会联网，也不需要密钥")

    def set_api_key(self):
        cur = ai_mod.find_api_key()
        if cur:
            tip = ("当前密钥：%s\n来源：%s\n\n粘贴新密钥可替换；留空并确定则清除。"
                   % (ai_mod.mask(cur), ai_mod.key_source()))
        else:
            tip = ("请粘贴 DeepSeek API Key（sk- 开头）。\n\n"
                   "没有的话到 platform.deepseek.com 创建一个。\n"
                   "费用极低：单次解释约 0.0015 元，且本地知识库命中时根本不调用。")
        text, ok = QInputDialog.getText(self, "设置 DeepSeek 密钥", tip, QLineEdit.Password)
        if not ok:
            return
        key = text.strip()
        path = config_mod.save_user_config({"ai": {"api_key": key}})
        self._refresh_ai_menu()
        if not key:
            QMessageBox.information(self, "已清除密钥", "密钥已清除。本地解释功能完全不受影响。")
            return
        QMessageBox.information(
            self, "已保存",
            "密钥已保存到用户配置：\n%s\n\n"
            "（刻意不放项目目录——这样把项目打包分享给别人时不会带出密钥）\n\n"
            "接下来测试一次连接。" % path)
        self.test_ai()

    def test_ai(self):
        if not ai_mod.has_api_key():
            r = QMessageBox.question(self, "还没配置密钥",
                                     "AI 补充需要 DeepSeek API Key。现在去设置吗？",
                                     QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.set_api_key()
            return
        self.act_ai_test.setEnabled(False)
        self.status.showMessage("正在测试 DeepSeek 连接…")
        self._ai_test_worker = AITestWorker(self)

        def done(ok, msg):
            self.act_ai_test.setEnabled(True)
            self.status.showMessage(("✓ " if ok else "✗ ") + msg.split("\n")[0], 12000)
            if ok:
                QMessageBox.information(self, "连接正常", msg)
            else:
                QMessageBox.warning(self, "连接失败", msg + "\n\n本地解释功能不受影响。")

        self._ai_test_worker.done.connect(done)
        self._ai_test_worker.start()

    def _ai_toggled(self, checked: bool):
        """勾选 AI 却没配密钥时，引导去设置——而不是让它默默失败。"""
        if checked and not ai_mod.has_api_key():
            r = QMessageBox.question(
                self, "还没配置密钥",
                "AI 补充需要 DeepSeek API Key，现在去设置吗？\n\n"
                "不设置也不影响本地解释——本地知识库命中时不联网、不花钱。",
                QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.set_api_key()
            if not ai_mod.has_api_key():
                self.chk_ai.blockSignals(True)          # 别递归触发自己
                self.chk_ai.setChecked(False)
                self.chk_ai.blockSignals(False)
                return
        try:
            config_mod.save_user_config({"ai": {"enabled": bool(checked)}})
        except Exception:                                       # noqa: BLE001
            pass

    def _mark_from_report(self, rep: dict) -> None:
        """把报告里的诊断标到编辑器上：选中的强红，其它错误浅红。"""
        self._mark_error = ""
        try:
            from engine.diagnostic import Diagnostic

            def make(src):
                return Diagnostic(compiler=rep.get("compiler") or "msvc",
                                  message=src.get("message", ""), code=src.get("code", ""),
                                  level=src.get("level", "error"),
                                  file=src.get("file", ""), line=src.get("line", 0),
                                  column=src.get("column", 0))

            diags = [make(d) for d in (rep.get("diagnostics") or [])]
            ch = rep.get("chosen") or {}
            chosen = make(ch) if ch.get("line") else None
            items = editorui_mod.items_from_diagnostics(diags, chosen)
            self.editor.mark_errors(items)
            if chosen is not None and chosen.line:
                self.editor.reveal_line(chosen.line)
        except Exception as e:                                  # noqa: BLE001
            # 不要静默吞掉：标红失败必须能查出来，否则就是"功能看起来没做"
            self._mark_error = "%s: %s" % (type(e).__name__, e)

    # ---------- 文件 ----------
    def _load_sample(self):
        self.editor.set_text(SAMPLE)
        self.editor.clear_marks()
        self.path = None
        self.lbl_file.setText("未命名（示例代码，含一个全角分号错误）")

    def open_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 C 源文件", self._projects_dir(),
                                           "C 源文件 (*.c *.h);;所有文件 (*.*)")
        if not p:
            return
        self._open_path(p)

    def save_file(self, force_dialog: bool = False):
        """保存。force_dialog=True 时强制弹「另存为」。"""
        target = None if force_dialog else self.path
        if not target:
            suggested = self.path or "untitled.c"
            target, _ = QFileDialog.getSaveFileName(self, "保存为", suggested, "C 源文件 (*.c)")
            if not target:
                return
        enc = self.cmb_enc.currentData()
        try:
            with open(target, "w", encoding=enc, newline="\n") as f:
                f.write(self.editor.text())
        except (OSError, LookupError) as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.path = target
        self.encoding = enc
        self.lbl_file.setText("%s　（%s）" % (target, encoding_label(enc)))
        self.status.showMessage("已保存：%s" % target, 4000)

    # ---------- 项目 ----------
    def _projects_dir(self) -> str:
        try:
            d = (config_mod.load().get("ui") or {}).get("projects_dir") or ""
        except Exception:                                       # noqa: BLE001
            d = ""
        return d or newproject_mod.default_projects_dir()

    def set_projects_dir(self):
        """设置以后新建项目默认放哪儿。"""
        cur = self._projects_dir()
        p = QFileDialog.getExistingDirectory(self, "选择项目默认存放位置", cur)
        if not p:
            return
        try:
            config_mod.save_user_config({"ui": {"projects_dir": p}})
        except Exception as e:                                  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.status.showMessage("以后新建项目默认放在：%s" % p, 6000)
        QMessageBox.information(self, "已设置",
                                "以后「新建项目」默认放在：\n%s" % p)

    def new_project(self):
        """新建项目：一个文件夹 + 一个 main.c，然后直接在编辑器里打开。"""
        dlg = newproject_mod.NewProjectDialog(self, self._projects_dir(),
                                              self.cmb_enc.currentData())
        # 坑：QDialog.Accepted 是**类属性**，写成 dlg.Accepted（实例）在 PySide6 下会
        # AttributeError，而且是在用户点「新建项目」时才炸。
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_path:
            return
        # 记住这次用的位置，下次新建直接落在这里
        try:
            config_mod.save_user_config({"ui": {"projects_dir": dlg.projects_dir}})
        except Exception:                                       # noqa: BLE001
            pass
        self._open_path(dlg.result_path)
        self.status.showMessage("已创建项目：%s" % dlg.result_path, 8000)

    def _open_path(self, path: str):
        """把某个文件读进编辑器（不弹文件选择框）。"""
        try:
            enc, _lines = read_source(path)
            with open(path, "rb") as f:
                text = f.read().decode(enc, errors="replace")
        except OSError as e:
            QMessageBox.warning(self, "打开失败", str(e))
            return
        self.path = path
        self.encoding = enc
        self.editor.clear_marks()
        for i, (_, code) in enumerate(ENCODINGS):
            if code == enc:
                self.cmb_enc.setCurrentIndex(i)
                break
        self.editor.set_text(text.replace("\r\n", "\n"))
        self.lbl_file.setText("%s　（%s）" % (path, encoding_label(enc)))

    def _encoding_changed(self):
        self.encoding = self.cmb_enc.currentData()

    # ---------- 检查 ----------
    def _working_copy(self) -> str:
        d = os.path.join(config_mod.path_of("build"), "panel")
        os.makedirs(d, exist_ok=True)
        base = os.path.basename(self.path) if self.path else "untitled.c"
        if not base.lower().endswith((".c", ".h")):
            base += ".c"
        out = os.path.join(d, base)
        enc = self.cmb_enc.currentData()
        try:
            data = self.editor.text().encode(enc, errors="replace")
        except LookupError:
            data = self.editor.text().encode("utf-8", errors="replace")
        with open(out, "wb") as f:
            f.write(data)
        return out

    def do_check(self):
        if self.worker is not None and self.worker.isRunning():
            return
        working = self._working_copy()          # 永远编译副本，不动原文件
        self.status.showMessage("正在编译…")
        self.btn_check.setEnabled(False)
        self._load_report_sync = None
        self.worker = CheckWorker(working, self.cmb_depth.currentData(),
                                  self.chk_ai.isChecked(), False, None, self)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_failed(self, tb: str):
        self.btn_check.setEnabled(True)
        self.status.showMessage("检查失败")
        self.view.setHtml('<pre style="color:#B91C1C;white-space:pre-wrap;">%s</pre>'
                          % html.escape(tb))

    # ---------- 运行 ----------
    def _start_run(self, mode: str):
        if self.run_worker is not None and self.run_worker.isRunning():
            return
        if self.worker is not None and self.worker.isRunning():
            return
        working = self._working_copy()
        enc = self.cmb_enc.currentData()
        self.status.showMessage("正在编译…")
        self.btn_run.setEnabled(False)
        self.btn_check.setEnabled(False)
        if mode == "panel":
            self.tabs.setCurrentIndex(1)        # 切到「运行输出」页
            self.view_run.setHtml('<div style="font-family:\'Microsoft YaHei\';'
                                  'padding:10px;color:#6B7280;">正在编译并运行…</div>')
        self.run_worker = RunWorker(working, self.txt_input.toPlainText(), enc, None,
                                    self.spin_timeout.value(), mode, self)
        self.run_worker.done.connect(self._on_run_done)
        self.run_worker.failed.connect(self._on_run_failed)
        self.run_worker.start()

    def do_run(self):
        """F9：在系统命令行窗口里运行。scanf 能真正交互输入，和书上的行为一致。"""
        self._start_run("console")

    def do_run_panel(self):
        """Shift+F9：在面板内运行并捕获输出，不弹窗口。"""
        self._start_run("panel")

    def _on_run_failed(self, tb: str):
        self.btn_run.setEnabled(True)
        self.btn_check.setEnabled(True)
        self.status.showMessage("运行失败")
        self.tabs.setCurrentIndex(1)
        self.view_run.setHtml('<pre style="color:#B91C1C;white-space:pre-wrap;">%s</pre>'
                              % html.escape(tb))

    def _on_run_done(self, res):
        from engine import run as run_mod

        self.btn_run.setEnabled(True)
        self.btn_check.setEnabled(True)

        if not res.compiled:
            # 编译没过：不在"运行输出"里堆一大堆编译器原文，但**第一条要留**——
            # 否则像"cannot open output file"这种工具层面的错误完全看不到。
            first = ""
            for d in (res.diagnostics or []):
                if getattr(d, "is_error", False):
                    first = "[%s] %s" % (getattr(d, "code", "") or d.level, d.message)
                    break
            extra = ('<pre style="font-family:Consolas,monospace;font-size:10pt;'
                     'color:#6B7280;white-space:pre-wrap;margin:10px 12px;">%s</pre>'
                     % html.escape(first)) if first else ""
            self.status.showMessage("编译没有通过，未运行")
            self.tabs.setCurrentIndex(1)
            self.view_run.setHtml(
                '<div style="font-family:\'Microsoft YaHei\';font-size:12pt;padding:12px;">'
                '<b style="color:#B91C1C;">编译没有通过，所以没有运行。</b>'
                '<div style="margin-top:8px;color:#374151;">'
                '先按 <b>F5</b> 看报错讲解 —— 代码得先能编译，才谈得上运行。</div></div>'
                + extra)
            return

        text = run_mod.format_result(res)
        # 用 <pre> 原样呈现：程序输出里的空格、缩进、制表符都是内容的一部分
        self.tabs.setCurrentIndex(1)
        self.view_run.setHtml(
            '<pre style="font-family:Consolas,\'Microsoft YaHei\',monospace;'
            'font-size:11pt;white-space:pre-wrap;margin:10px;">%s</pre>' % html.escape(text))

        # 状态栏要给"具体是哪一种失败"，不能笼统说一句"没成功" ——
        # 「多半是死循环」这类提示是初学者最需要的那句话。
        if res.timed_out:
            self.status.showMessage("运行超时已终止（多半是死循环，检查循环条件和 i++）")
        elif res.ok and "系统命令行" in " ".join(res.notes):
            self.status.showMessage("已在系统命令行窗口里运行（在那边输入和查看输出）")
        elif res.ok:
            self.status.showMessage("运行结束　用时 %.2f 秒" % res.duration)
        elif res.exit_code:
            self.status.showMessage("程序异常退出（返回码 %d），原因见下方" % res.exit_code)
        else:
            self.status.showMessage("运行没有成功，详情见下方")

    def _on_done(self, rep: dict):
        self.btn_check.setEnabled(True)
        self._last_report = rep
        self._render_report(rep)
        # 把光标移到出错行
        ch = rep.get("chosen") or {}
        if ch.get("line"):
            self.editor.goto(ch["line"], ch.get("column") or 1)
        n = len(rep.get("diagnostics") or [])
        self.status.showMessage(
            "编译器 %s　诊断 %d 条　%s" % (
                rep.get("compiler"), n,
                "；".join(rep.get("notes") or []) or "SARIF %s" % rep.get("sarif_status")))

    def _rerender_current(self):
        if getattr(self, "_last_report", None):
            self._render_report(self._last_report)

    def _render_report(self, rep: dict):
        """把结构化报告渲染成 HTML，并在编辑器里把出错位置标红。"""
        self._mark_from_report(rep)
        diags = rep.get("diagnostics") or []
        head = []
        if rep.get("timed_out"):
            head.append('<div style="color:#B91C1C;">编译超时，已终止编译器进程。</div>')
        for note in rep.get("notes") or []:
            head.append('<div style="color:#B45309;">（%s）</div>' % html.escape(note))

        if not diags:
            body = ('<div style="font-family:\'Microsoft YaHei\',sans-serif;font-size:12pt;'
                    'color:#15803D;padding:12px;">编译通过，没有发现任何错误或警告。</div>')
            self._explanation_html = "".join(head) + body
            self._show_explanation()
            return

        exp = rep.get("explanation")
        if not exp:
            self._explanation_html = "".join(head) + "<p>没有可展示的诊断。</p>"
            self._show_explanation()
            return

        depth = self.cmb_depth.currentData()
        # 用与命令行同一套 render_html，保证两处结论一致
        from engine import render as render_mod
        from engine import explain as explain_mod
        from engine.kb import KnowledgeBase
        cfg = config_mod.load()
        kb = KnowledgeBase.load(rep.get("compiler") or "msvc")
        chosen = rep.get("chosen") or {}
        from engine.diagnostic import Diagnostic
        d = Diagnostic(compiler=rep.get("compiler") or "msvc",
                       message=chosen.get("message", ""), code=chosen.get("code", ""),
                       level=chosen.get("level", "error"), file=chosen.get("file", ""),
                       line=chosen.get("line", 0), column=chosen.get("column", 0))
        e = explain_mod.build(d, kb)
        e.ai = exp.get("ai")
        e.ai_meta = exp.get("ai_meta", "")

        extra = ""
        if len(diags) > 1:
            # 「雪崩」提示：编译器常常一次喷几十条，但它们绝大多数是第一条的**连锁反应**。
            # 不明说这一点，初学者会以为自己的代码错得一塌糊涂，直接放弃。
            extra = ('<div style="color:#6B7280;font-size:10pt;margin-bottom:8px;">'
                     '（共 %d 条诊断，这里只讲最可能相关的一条）</div>' % len(diags))
            if len(diags) >= 3:
                extra += ('<div style="color:#B45309;font-size:10pt;margin-bottom:8px;">'
                          '提示：一次报很多条通常**不是很多个错误**——'
                          '第一条往往是根因，修好它之后后面那些多半会一起消失。'
                          '先只改下面讲的那一处，再重新检查。</div>')
        self._explanation_html = "".join(head) + extra + render_mod.render_html(e, depth)
        self._show_explanation()

    def _show_explanation(self):
        """展示讲解。若「使用说明」正开着，自动收起——用户按了检查就是想看结果。"""
        self.tabs.setCurrentIndex(0)             # 切回「讲解」页
        if self.act_help.isChecked():
            self.act_help.setChecked(False)      # 触发 _apply_view
        else:
            self._apply_view()

    # ---------- 缩放（Ctrl + 滚轮 / Ctrl +/-） ----------
    def eventFilter(self, obj, ev):
        """在**应用层面**接管缩放。

        为什么不靠 QAction 的快捷键：
        Monaco 是内嵌的 Chromium。Qt 在派发按键前会先发一个 ShortcutOverride 事件，
        如果焦点控件接受了它，快捷键就**整个不触发**——表现是"按了没反应"，
        而且不报任何错。挂在 QApplication 上的事件过滤器在事件送到焦点控件
        **之前**就能看到它，从根上绕开这个问题，也顺便省掉"窗口是否活跃"的前提。
        """
        try:
            if ev.type() == QEvent.KeyPress and (ev.modifiers() & Qt.ControlModifier):
                k = ev.key()
                if k in (Qt.Key_Equal, Qt.Key_Plus):
                    self.zoom_by(1)
                    return True
                if k in (Qt.Key_Minus, Qt.Key_Underscore):
                    self.zoom_by(-1)
                    return True
                if k == Qt.Key_0:
                    self.zoom_reset()
                    return True
            if ev.type() == QEvent.Wheel and (ev.modifiers() & Qt.ControlModifier):
                # 编辑器、讲解区、运行输出区，Ctrl+滚轮都能缩放
                self.zoom_by(1 if ev.angleDelta().y() > 0 else -1)
                return True
        except Exception:                                       # noqa: BLE001
            pass
        return super().eventFilter(obj, ev)

    def zoom_by(self, delta: int) -> None:
        self._zoom = max(-6, min(14, self._zoom + delta))
        self._apply_zoom()
        self._save_zoom()

    def zoom_reset(self) -> None:
        self._zoom = 0
        self._apply_zoom()
        self._save_zoom()

    def _apply_zoom(self):
        """把缩放同时应用到编辑器与两个输出区。

        QTextBrowser 的 zoomIn/zoomOut 是**累加**的，所以这里记录"已经应用了多少"，
        每次只做差值，避免来回缩放后状态漂移（那样 Ctrl+0 就回不到默认了）。
        """
        want = self._zoom
        diff = want - getattr(self, "_applied_zoom", 0)
        if diff:
            for w in (self.view, self.view_run):
                try:
                    if diff > 0:
                        w.zoomIn(diff)
                    else:
                        w.zoomOut(-diff)
                except Exception:                               # noqa: BLE001
                    pass
            self._applied_zoom = want
        try:
            self.editor.set_zoom(want)
        except Exception:                                       # noqa: BLE001
            pass
        if want:
            self.status.showMessage("缩放：%+d 档（Ctrl+0 恢复默认）" % want)

    def _save_zoom(self):
        try:
            config_mod.save_user_config({"ui": {"zoom": self._zoom}})
        except Exception:                                       # noqa: BLE001
            pass


def _install_crash_handler():
    """无控制台启动时，异常会静默消失。把崩溃写进日志并弹窗，避免"双击没反应"。"""
    import datetime

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log_dir = config_mod.path_of("log")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "panel_crash.log"), "a", encoding="utf-8") as f:
                f.write("\n===== %s =====\n%s" % (
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
        except Exception:                                       # noqa: BLE001
            pass
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(None, "ctutor 启动失败",
                                     "面板遇到了一个错误，详情已写入 log/panel_crash.log：\n\n"
                                     + text[-800:])
        except Exception:                                       # noqa: BLE001
            pass
    sys.excepthook = hook


def _gcc_hint():
    """自检报告里带上「找到的 gcc」——打包后是否能编译，一眼可见。"""
    try:
        from engine import compilers as _c
        return _c.find_gcc() or ""
    except Exception:                                           # noqa: BLE001
        return ""


def _say(msg):
    """打包成 windowed exe 后 sys.stdout 是 None，print 会出问题——统一走这里。"""
    try:
        if sys.stdout is not None:
            print(msg)
    except Exception:                                           # noqa: BLE001
        pass


def _write_selftest_json(png_path, payload):
    """把自检结果写成 JSON，放在截图旁边。

    为什么需要：windowed exe 没有控制台，光看打印信息没法做无人值守验证。
    写文件才能让打包出来的 exe 被自动化检查。
    """
    path = os.path.splitext(png_path)[0] + ".json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        return ""
    return path


# ---------------------------------------------------------------- 启动
def main(argv=None) -> int:
    _install_crash_handler()
    ap = argparse.ArgumentParser(prog="ctutor-panel")
    ap.add_argument("file", nargs="?", help="要打开的 C 文件")
    ap.add_argument("--selftest", metavar="PNG",
                    help="离屏渲染一张截图后退出（无人值守验证用）")
    ap.add_argument("--offscreen", action="store_true", help="强制离屏渲染")
    args = ap.parse_args(argv)

    if args.selftest or args.offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QApplication(sys.argv[:1])
    font_family = ensure_cjk_font(app)
    win = MainWindow()
    win.font_family = font_family
    if args.file and os.path.exists(args.file):
        win.path = os.path.abspath(args.file)
        try:
            with open(args.file, "rb") as f:
                raw = f.read()
            enc = detect_source_encoding(args.file)
            win.encoding = enc
            win.editor.set_text(raw.decode(enc, errors="replace").replace("\r\n", "\n"))
            win.lbl_file.setText("%s　（%s）" % (args.file, encoding_label(enc)))
        except OSError:
            pass
    win.show()

    if args.selftest:
        # 无人值守：同步跑一次检查，等 Monaco 加载后截图，并把结果写成 JSON
        comp_info = {}
        for i in range(60):
            app.processEvents()
            time.sleep(0.1)
            if i == 25:
                win.editor.probe_completion(lambda info: comp_info.update(info))
        rep = {}
        check_error = ""
        try:
            rep = report_mod.check_once(win._working_copy(),
                                        depth=win.cmb_depth.currentData(),
                                        workdir=os.path.join(
                                            config_mod.path_of("build"), "panel_run"))
            win._last_report = rep
            win._render_report(rep)
        except Exception:                                       # noqa: BLE001
            check_error = traceback.format_exc()
            win.view.setHtml("<pre>%s</pre>" % html.escape(check_error))
        for _ in range(20):
            app.processEvents()
            time.sleep(0.1)
        # 标红是异步的（runJavaScript），截图前确认状态
        mark_info = {}
        win.editor.probe_marks(lambda info: mark_info.update({"state": info}))
        for _ in range(15):
            app.processEvents()
            time.sleep(0.1)

        # 顺带验证"运行"功能在打包版里也能用（这是唯一会真的执行学生代码的路径）
        run_info = {}
        try:
            from engine import run as run_mod
            rwd = os.path.join(config_mod.path_of("build"), "panel_selftest_run")
            enc = win.cmb_enc.currentData()

            # (a) 编辑区里是故意写错的示例：应当编译不过、并明确拒绝运行
            rr_bad = run_mod.run_once(win._working_copy(), rwd, stdin_text="",
                                      timeout=10, source_encoding=enc)
            # (b) 另写一个正常程序：应当真的跑起来并拿到输出
            os.makedirs(rwd, exist_ok=True)
            good = os.path.join(rwd, "selftest_ok.c")
            with open(good, "w", encoding="utf-8", newline="\n") as f:
                f.write('#include <stdio.h>\n'
                        'int main(void){int a,b;if(scanf("%d %d",&a,&b)!=2)return 1;'
                        'printf("sum=%d\\n",a+b);return 0;}\n')
            rr_ok = run_mod.run_once(good, os.path.join(rwd, "ok"), stdin_text="3 4",
                                     timeout=10, source_encoding="utf-8")
            run_info = {"gate_blocks_bad": (not rr_bad.compiled),
                        "ok_compiled": rr_ok.compiled, "ok_ok": rr_ok.ok,
                        "ok_stdout": (rr_ok.stdout or "").strip()[:60],
                        "exit_code": rr_ok.exit_code}
            # 顺手把运行输出渲染到界面上，好截第二张图给用户看
            win.txt_input.setPlainText("3 4")
            win._on_run_done(rr_ok)
            for _ in range(8):
                app.processEvents()
                time.sleep(0.1)
        except Exception as e:                                  # noqa: BLE001
            run_info = {"error": "%s: %s" % (type(e).__name__, e)}

        zoom_info = {}
        win.zoom_by(2)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.1)
        win.editor.probe_zoom(lambda info: zoom_info.update(info))
        for _ in range(10):
            app.processEvents()
            time.sleep(0.1)
        win.zoom_reset()
        for _ in range(6):
            app.processEvents()
            time.sleep(0.1)

        # 截图目录可能不存在（全新克隆时 tests/build/ 被 .gitignore 忽略），
        # 不建的话 grab().save() 会静默失败、返回非 0 —— 表现是"测试莫名其妙挂了"。
        try:
            d = os.path.dirname(os.path.abspath(args.selftest))
            if d:
                os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        ok = win.grab().save(args.selftest)
        # 再来一张「运行输出」页的图，便于直观看到运行功能
        run_shot = ""
        try:
            win.tabs.setCurrentIndex(1)
            for _ in range(6):
                app.processEvents()
                time.sleep(0.1)
            run_shot = os.path.splitext(args.selftest)[0] + "_run.png"
            win.grab().save(run_shot)
        except Exception:                                       # noqa: BLE001
            run_shot = ""

        ch = rep.get("chosen") or {}
        payload = {
            "ok": bool(ok),
            "frozen": bool(getattr(sys, "frozen", False)),
            "app_root": config_mod.ROOT,
            "editor": "Monaco" if win.editor.monaco else "fallback",
            "font": getattr(win, "font_family", "") or "system-default",
            "completion": comp_info,
            "mark": mark_info.get("state") or {},
            "mark_error": getattr(win, "_mark_error", ""),
            "run": run_info,
            "zoom": zoom_info,
            "check": {
                "error": check_error,
                "compiler": rep.get("compiler"),
                "sarif_status": rep.get("sarif_status"),
                "notes": rep.get("notes"),
                "diagnostics": len(rep.get("diagnostics") or []),
                "chosen_code": ch.get("code"),
                "chosen_line": ch.get("line"),
                "explanation_matched": bool((rep.get("explanation") or {}).get("matched")),
                "ai_meta": (rep.get("explanation") or {}).get("ai_meta", ""),
            },
            "data_dir": config_mod.path_of("data"),
            "bin_gcc_found": bool(_gcc_hint()),
        }
        jpath = _write_selftest_json(args.selftest, payload)
        _say("截图已保存：%s（成功=%s，编辑器=%s，中文字体=%s）"
             % (args.selftest, ok, payload["editor"], payload["font"]))
        _say("补全状态：%s" % json.dumps(comp_info, ensure_ascii=False))
        _say("标红状态：%s" % json.dumps(mark_info.get("state") or {}, ensure_ascii=False))
        _say("运行状态：%s" % json.dumps(run_info, ensure_ascii=False))
        _say("缩放状态：%s" % json.dumps(zoom_info, ensure_ascii=False))
        _say("自检报告：%s" % jpath)
        _say(json.dumps(payload["check"], ensure_ascii=False))
        return 0 if ok else 1

    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:                                       # noqa: BLE001
        _install_crash_handler()
        sys.excepthook(*sys.exc_info())
        raise SystemExit(1)
