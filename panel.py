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
import queue
import sys
import tempfile
import threading
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

from PySide6.QtCore import (Qt, QEvent, QEasingCurve, QObject,        # noqa: E402
                            QPropertyAnimation, QThread, QTimer, Signal)
from PySide6.QtGui import (QAction, QFont, QFontDatabase, QKeySequence,  # noqa: E402
                           QTextCursor)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,   # noqa: E402
                               QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow,
                               QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QSizePolicy, QSpinBox, QSplitter, QTabBar, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)
from engine import ai as ai_mod                                     # noqa: E402
from engine import ai_fix as ai_fix_mod                             # noqa: E402
from engine import completion as completion_mod                     # noqa: E402
from engine import config as config_mod                             # noqa: E402
from engine import compilers as compilers_mod                       # noqa: E402
from engine import editorui as editorui_mod                         # noqa: E402
from engine import format as format_mod                             # noqa: E402
from engine import report as report_mod                             # noqa: E402
from engine import session as session_mod                           # noqa: E402
import newproject as newproject_mod                                 # noqa: E402
from engine.encoding import detect_source_encoding, encoding_label, read_source  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
# 右键菜单现场快照默认关闭：只是排查用的，不该在每次启动时花 240 次页面 JS。
_MENU_DEBUG = os.environ.get("CTUTOR_MENU_DEBUG", "") in ("1", "true", "on")
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

    # ---- 右键菜单扩展 + 锚点 ----
    def add_context_action(self, action_id: str, label: str) -> bool:
        """往编辑器右键菜单里加一条命令。

        qtmonaco 自带这个扩展点（add_action + context_menu_action_triggered），
        所以不用自己搭 QWebChannel、也不用改第三方包。
        """
        if not self.monaco:
            return False
        try:
            self.widget.add_action(action_id, label, language="c")
            return True
        except Exception:                                           # noqa: BLE001
            return False

    def set_anchors(self, items, callback=None) -> None:
        """把锚点画到编辑器里。纯装饰器：不新增、不修改任何字符。"""
        if not self.monaco:
            return
        js = "window.__ctutor_set_anchors(%s)" % json.dumps(items, ensure_ascii=False)
        try:
            if callback is None:
                self.widget.page().runJavaScript(js)
            else:
                self.widget.page().runJavaScript(js, callback)
        except Exception:                                           # noqa: BLE001
            pass

    def goto_anchor(self, line: int) -> None:
        """跳到某一行，并把它滚到屏幕中间。"""
        if not self.monaco:
            try:
                from PySide6.QtGui import QTextCursor

                cur = self.widget.textCursor()
                cur.movePosition(QTextCursor.Start)
                cur.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor,
                                 max(0, int(line) - 1))
                self.widget.setTextCursor(cur)
                self.widget.centerCursor()
            except Exception:                                       # noqa: BLE001
                pass
            return
        try:
            self.widget.page().runJavaScript(
                "window.__ctutor_goto_anchor(%d)" % int(line))
        except Exception:                                           # noqa: BLE001
            pass

    def cursor_info(self, callback) -> None:
        """取当前光标行号和该行原文（回调一个 JSON 字符串）。"""
        if not self.monaco:
            try:
                cur = self.widget.textCursor()
                callback(json.dumps({"line": cur.blockNumber() + 1,
                                     "text": cur.block().text()}, ensure_ascii=False))
            except Exception:                                       # noqa: BLE001
                callback("{}")
            return
        try:
            self.widget.page().runJavaScript("window.__ctutor_cursor_info()", callback)
        except Exception:                                           # noqa: BLE001
            callback("{}")

    def eval_js(self, code, callback=None) -> bool:
        """跑一段 JS（可选回调）。失败不抛异常，只返回 False。"""
        if not self.monaco:
            return False
        try:
            if callback is None:
                self.widget.page().runJavaScript(code)
            else:
                self.widget.page().runJavaScript(code, callback)
            return True
        except Exception:                                           # noqa: BLE001
            return False


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
    """在后台编译并运行程序，输出捕获回面板。

    必须放后台：程序可能有死循环（就算有超时，卡在主线程里界面也会冻住）；
    而且要运行就得先编译，编译本身也要几百毫秒到几秒。

    不再打开系统命令行窗口：以前 F9 会弹一个黑框，学生机器上容易误以为"卡住/
    中毒"，而且黑框和面板两处输出容易对不上。现在统一显示在「运行输出」页。
    """

    done = Signal(object)
    failed = Signal(str)
    chunk = Signal(str)          # 程序跑到一半就有输出了：边跑边发，界面实时显示

    def __init__(self, working_path, stdin_text, source_encoding, prefer,
                 timeout=10, parent=None):
        super().__init__(parent)
        self.working_path = working_path
        self.initial_input = stdin_text or ""
        self.source_encoding = source_encoding
        self.prefer = prefer
        self.timeout = timeout
        # 半交互的输：界面线程往里放一行，后台线程写进程序的 stdin
        self._stdin = queue.Queue()
        self._eof_sent = False          # EOF 只能送一次，重复送会提前关掉管道
        self._eof_lock = threading.Lock()
        self._stop = threading.Event()  # 「停止」按钮：引擎看到它就把进程树杀掉

    def stop(self) -> bool:
        """界面线程调用：立刻停止运行（连子进程一起杀）。重复点只算一次。"""
        if self._stop.is_set():
            return False
        self._stop.set()
        try:
            self._stdin.put(None)       # 顺手关掉 stdin，程序若正好在读就立刻读到 EOF
        except Exception:                                   # noqa: BLE001
            pass
        return True

    def send(self, text: str):
        """界面线程调用：给正在运行的程序送一行输入。"""
        if not text.endswith("\n"):
            text += "\n"
        self._stdin.put(text)

    def end_input(self) -> bool:
        """界面线程调用：没有更多输入了（EOF）。重复点只算一次。"""
        with self._eof_lock:
            if self._eof_sent:
                return False
            self._eof_sent = True
        self._stdin.put(None)
        return True

    def run(self):
        try:
            from engine import run as run_mod

            # 每次运行用独立目录：上一次运行可能还锁着 a.exe，
            # 共用目录会让下一次编译直接失败，报成"编译没有通过"，极难排查。
            wd = run_mod.unique_workdir(
                os.path.join(config_mod.path_of("build"), "panel_run"))
            comp = compilers_mod.detect(self.prefer, config_mod.load())
            if self.initial_input.strip():
                self.send(self.initial_input)      # 运行前就填好的，先一次性给它
            res = run_mod.run_once(self.working_path, wd,
                                   timeout=self.timeout,
                                   source_encoding=self.source_encoding,
                                   compiler=comp,
                                   on_output=self.chunk.emit,
                                   stdin_queue=self._stdin,
                                   stop_event=self._stop)
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


# ---------------------------------------------------------------- 文档模型
def _norm_text(s: str) -> str:
    """统一成 \n 换行。

    实测：Monaco 会把文本里的 \\n 归一成 \\r\\n 返回。如果不统一，
    "刚打开的文件"和"模型里的基线"永远差一个 \\r，于是**每个文件一打开就被判成已修改**，
    退出时会白问一遍"要保存吗"。
    """
    return s.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------- 编译器预热
_WARM = {"started": False}


def _start_compiler_warmup() -> None:
    """在后台把编译器环境初始化好（只做一次）。

    为了什么：MSVC 每次编译前都要先跑 vcvars64.bat 初始化环境，实测约 1.8 秒，
    而 cl 真正编译一个小文件只要约 0.1 秒——也就是说「按 F5 等两秒」里，
    九成以上时间花在重复初始化上。引擎侧已经把这份环境缓存起来（只捕获一次），
    这里再把那一次捕获挪到程序启动的后台，学生第一次检查也是秒出。

    用 daemon 线程而不是 QThread：进程退出时它随解释器一起结束，
    不会出现"界面已关闭但线程还在跑"的 Qt 崩溃/卡死。
    """
    if _WARM["started"]:
        return
    _WARM["started"] = True

    def work():
        try:
            cfg = config_mod.load()
            comp = compilers_mod.detect(None, cfg)
            if isinstance(comp, compilers_mod.MsvcCompiler):
                compilers_mod.MsvcCompiler._fast_path(
                    comp.vcvars, config_mod.path_of("build", cfg))
        except Exception:                                       # noqa: BLE001
            pass

    try:
        threading.Thread(target=work, name="ctutor-warmup", daemon=True).start()
    except Exception:                                           # noqa: BLE001
        pass


class Doc:
    """一个打开的文件。

    为什么多个项目**共用一个编辑器**而不是每个标签一个 Monaco：
    Monaco 是内嵌的 Chromium，一个实例就要几百 MB 内存；开 5 个标签等于开 5 个浏览器。
    所以这里用"标签栏 + 单编辑器"：切标签时把当前编辑框里的文本存回模型、
    再把目标文档的文本灌进去。对初学者那几个小文件来说，切换是瞬间的。

    text 是"最后一次同步时的文本"，用来判断有没有改动（和编辑器里的实时文本比对）。
    """

    __slots__ = ("path", "encoding", "text", "saved_text", "label", "modified",
                 "marks", "format_undo", "compare_old", "compare_new", "anchors",
                 "input_text")

    def __init__(self, path=None, encoding="mbcs", text="", label="未命名"):
        self.path = path
        self.encoding = encoding
        self.text = text
        self.saved_text = text     # 上次保存/打开时的内容，用来判断有没有改动
        self.label = label
        self.modified = False
        self.marks = []          # 切回来时恢复上一轮的标红
        self.format_undo = None  # 上一次"整体改写"之前的文本（对齐 / AI 修改共用）
        self.compare_old = ""    # AI 修改前
        self.compare_new = ""    # AI 修改后
        # 锚点：{"name": str, "line": int, "text": str}
        # 只是显示用的书签，**不写进 .c 文件**，也不算"文件被改动"
        self.anchors = []
        # 这个文档自己的"程序输入"。不共享是有原因的：上个项目填的测试数据
        # 留在输入框里，切到另一个项目按 F9 就把它喂给了别的程序。
        self.input_text = ""

    def title(self) -> str:
        base = os.path.basename(self.path) if self.path else self.label
        return ("* " + base) if self.modified else base

    def tooltip(self) -> str:
        if self.path:
            return "%s\n（%s）" % (self.path, encoding_label(self.encoding))
        return "还没保存到文件"


class AIFixWorker(QThread):
    """后台线程：让模型给一份修正后的完整代码。

    单独开线程的原因和检查一样：网络往返几秒到十几秒，卡在 UI 线程会假死。
    """
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, diag, source, code_context, parent=None):
        super().__init__(parent)
        self.diag = diag
        self.source = source
        self.code_context = code_context

    def run(self):
        try:
            res = ai_fix_mod.request_fix(self.diag, self.source, self.code_context)
            self.done.emit(res)
        except Exception:                                           # noqa: BLE001
            self.failed.emit(traceback.format_exc())


class AIFixModeDialog(QDialog):
    """先问清楚：AI 改完之后，你想怎么对照？

    三种方式各有各的用处，所以每次让用户选，而不是替他决定：
      · 原位修改 + 对照按钮 —— 改完直接看到结果，想对比再点按钮
      · 独立窗口          —— 左边原代码右边新代码，可以拖窗口并排看
      · 行内注释          —— 不动原代码，只在问题行下面加一行 // AI 建议
    """

    MODES = [
        ("inplace", "直接在原文里改，改完用「对照」按钮比一比",
         "最省地方。标签会带 * 号提醒你还没存盘；不满意可以一键还原。"),
        ("window", "另开一个窗口显示正确代码，两个窗口并排对照",
         "原代码一个字都不动，看完再决定要不要应用。窗口可以随意拖动、缩放。"),
        ("inline", "在出问题的那一行下面加一行 // AI 建议",
         "原代码不动，只是加注释。适合想自己动手改、只要个提示的时候。"),
    ]

    def __init__(self, parent=None, current="inplace"):
        super().__init__(parent)
        self.setWindowTitle("AI 修改 — 选择对照方式")
        self.resize(620, 380)
        self.mode = current or "inplace"
        lay = QVBoxLayout(self)
        tip = QLabel("AI 会按这个代码给你一份修正版本。先选你想怎么看改了什么：")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self._radios = []
        for key, title, desc in self.MODES:
            row = QVBoxLayout()
            rd = QRadioButton(title)
            rd.setStyleSheet("font-weight:600;")
            row.addWidget(rd)
            d = QLabel("　　" + desc)
            d.setStyleSheet("color:#4B5563;font-size:10pt;")
            d.setWordWrap(True)
            row.addWidget(d)
            box = QWidget()
            box.setLayout(row)
            lay.addWidget(box)
            self._radios.append((key, rd))
            if key == self.mode:
                rd.setChecked(True)
        if not any(rd.isChecked() for _k, rd in self._radios):
            self._radios[0][1].setChecked(True)

        self.chk_remember = QCheckBox("记住这个选择，以后不再问")
        lay.addWidget(self.chk_remember)
        lay.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始修改")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept(self):
        for key, rd in self._radios:
            if rd.isChecked():
                self.mode = key
                break
        self.accept()

    def remember(self) -> bool:
        return self.chk_remember.isChecked()


class DiffDialog(QDialog):
    """原代码 / 新代码的行级对照。"""

    def __init__(self, parent=None, old="", new="", title="对照：改了什么"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(900, 700)

        add, dele = ai_fix_mod.diff_stats(old, new)
        rows = ai_fix_mod.diff_lines(old, new)
        body = []
        lno = 0
        for tag, text in rows:
            lno += 1
            esc = html.escape(text).replace(" ", "&nbsp;")
            if tag == "add":
                body.append('<div style="background:#DCFCE7;">'
                            '<span style="color:#15803D;">+ </span>%s</div>' % esc)
            elif tag == "del":
                body.append('<div style="background:#FEE2E2;">'
                            '<span style="color:#B91C1C;">- </span>%s</div>' % esc)
            else:
                body.append('<div><span style="color:#9CA3AF;">  </span>%s</div>' % esc)
        doc = (
            '<div style="font-family:\'Consolas\',\'Microsoft YaHei\',monospace;'
            'font-size:11pt;line-height:1.5;white-space:pre;">'
            '<p style="font-family:\'Microsoft YaHei\',sans-serif;">'
            '绿色是新增，红色是删掉的，灰色是没动的。'
            '　<span style="color:#15803D;">+%d 行</span>　'
            '<span style="color:#B91C1C;">-%d 行</span></p>'
            '%s</div>' % (add, dele, "".join(body))
        )
        self.view = QTextBrowser()
        self.view.setStyleSheet("QTextBrowser{background:#FFFFFF;border:1px solid #dde3ea;}")
        self.view.setHtml(doc)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)


class FixedCodeWindow(QDialog):
    """独立窗口：显示 AI 给的正确代码，可拖动可缩放，看完再决定要不要应用。

    刻意做成**非模态**（show 而不是 exec）：这样学生可以一边看这个窗口里的代码，
    一边在原窗口里改，或者拖开并排对照。
    """

    applied = Signal()

    def __init__(self, parent=None, code="", changes=None, summary=""):
        super().__init__(parent)
        self.setWindowTitle("AI 给的正确代码")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(820, 640)
        self._code = code
        lay = QVBoxLayout(self)

        head = QLabel(summary or "下面是 AI 修正后的完整代码。原文件没有任何改动。")
        head.setWordWrap(True)
        head.setStyleSheet("color:#374151;")
        lay.addWidget(head)

        if changes:
            items = []
            for ch in changes[:8]:
                bits = []
                if ch.get("before") and ch.get("after"):
                    bits.append("%s → %s" % (ch.get("before"), ch.get("after")))
                elif ch.get("after"):
                    bits.append(ch.get("after"))
                if ch.get("why"):
                    bits.append(ch.get("why"))
                if bits:
                    items.append("第 %s 行：%s" % (ch.get("line") or "?", "；".join(bits)))
            if items:
                info = QLabel("改了这些：\n" + "\n".join("· " + it for it in items))
                info.setWordWrap(True)
                info.setStyleSheet("color:#4B5563;font-size:10pt;")
                lay.addWidget(info)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setPlainText(code)
        self.view.setStyleSheet(
            "QPlainTextEdit{font-family:'Consolas','Microsoft YaHei',monospace;"
            "font-size:11pt;background:#FCFCFD;}")
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        btn_copy = QPushButton("复制全部")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self._code))
        btn_apply = QPushButton("应用到编辑器")
        btn_apply.setDefault(True)
        btn_apply.clicked.connect(self._apply)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_copy)
        row.addStretch(1)
        row.addWidget(btn_apply)
        row.addWidget(btn_close)
        lay.addLayout(row)

    def _apply(self):
        self.applied.emit()
        self.accept()


# ---------------------------------------------------------------- 平滑滚动
class SmoothScroller(QObject):
    """把 QTextBrowser 的滚轮滚动变成短动画。

    原生滚轮是"一格一跳"（约三行），看长讲解时视觉很跳，尤其是读到一半
    被打断后很难找回位置。这里拦下滚轮事件，改成对滚动条做 160ms 缓动。

    只接管 wheel 事件：键盘翻页、拖动滚动条、点链接都不受影响。
    连续滚滚轮时目标位置会累加，不会互相打断。
    """

    def __init__(self, view, duration: int = 150, ratio: float = 0.16):
        super().__init__(view)
        self.view = view
        self.ratio = ratio
        self._target = None
        self._anim = QPropertyAnimation(view.verticalScrollBar(), b"value", self)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_finished)
        try:
            view.viewport().installEventFilter(self)
        except Exception:                                       # noqa: BLE001
            pass

    def _on_finished(self):
        self._target = None

    def reset(self):
        """换内容之前调用：停掉动画，否则新页面会被旧动画拽着跑。"""
        try:
            self._anim.stop()
        except Exception:                                       # noqa: BLE001
            pass
        self._target = None

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.Wheel:
            return False
        try:
            if obj is not self.view.viewport():
                return False
            sb = self.view.verticalScrollBar()
            if sb.maximum() <= sb.minimum():
                return False
            delta = ev.angleDelta().y()
            if not delta:
                return False
            unit = max(24, int(self.view.viewport().height() * self.ratio))
            start = self._target if self._target is not None else sb.value()
            target = start - int(delta / 120.0 * unit)
            target = max(sb.minimum(), min(sb.maximum(), target))
            if target == sb.value() and self._target is None:
                return False
            self._target = target
            self._anim.stop()
            self._anim.setStartValue(sb.value())
            self._anim.setEndValue(target)
            self._anim.start()
            return True
        except Exception:                                       # noqa: BLE001
            return False


# ---------------------------------------------------------------- 使用说明窗口
class HelpDialog(QDialog):
    """独立的使用说明窗口。

    为什么从「讲解栏」里搬出来：
      以前按 F1 是把讲解栏的内容临时换成说明。问题是——
        · 说明和讲解抢同一块地方，看说明时看不到刚才的讲解；
        · 按检查时又要把说明收起来，用户会以为"按了没反应"；
        · 说明里有很多条目，塞在下半屏里要滚很久。
      改成独立窗口后：说明可以一直开着对照着操作，讲解栏只讲报错。
    """

    def __init__(self, parent=None, html=""):
        super().__init__(parent)
        self.setWindowTitle("使用说明")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(780, 660)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet("QTextBrowser{background:#FFFFFF;border:1px solid #dde3ea;}")
        self.view.setHtml(html)
        btn = QPushButton("知道了，关闭")
        btn.setDefault(True)
        btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)


# ---------------------------------------------------------------- 锚点列表
class AnchorDialog(QDialog):
    """锚点列表：双击跳转，也可以在这儿重命名/删除。

    为什么用列表而不是下拉菜单：锚点是要「一眼扫过去找到目标」的东西，
    列表能同时显示名字、行号和那行代码，下拉菜单只能显示一行文字。
    """

    def __init__(self, anchors, parent=None):
        super().__init__(parent)
        self.setWindowTitle("锚点")
        self.resize(560, 420)
        self.action = ""          # goto / rename / delete / ""
        self.index = -1

        tip = QLabel("双击直接跳过去。锚点只是书签，不会往代码里插任何东西。")
        tip.setStyleSheet("color:#4B5563;")
        tip.setWordWrap(True)

        self.list = QListWidget()
        for i, a in enumerate(anchors or []):
            line = int(a.get("line") or 1)
            text = (a.get("text") or "").strip()
            label = "%s" % (a.get("name") or "未命名")
            if text:
                label += "    · 第 %d 行    · %s" % (line, text[:60])
            else:
                label += "    · 第 %d 行" % line
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, i)
            it.setToolTip("第 %d 行：%s" % (line, text))
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(self._on_double)

        self.btn_goto = QPushButton("跳转")
        self.btn_goto.setDefault(True)
        self.btn_goto.clicked.connect(lambda: self._done("goto"))
        self.btn_rename = QPushButton("重命名")
        self.btn_rename.clicked.connect(lambda: self._done("rename"))
        self.btn_del = QPushButton("删除")
        self.btn_del.clicked.connect(lambda: self._done("delete"))
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(self.btn_goto)
        row.addWidget(self.btn_rename)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        row.addWidget(self.btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)

        has = bool(anchors)
        for b in (self.btn_goto, self.btn_rename, self.btn_del):
            b.setEnabled(has)

    def _on_double(self, _item):
        self._done("goto")

    def _done(self, action):
        row = self.list.currentRow()
        if row < 0:
            return
        self.action = action
        self.index = row
        self.accept()


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
        # 多文档：一个标签 = 一个打开的文件（可以是不同项目里的）
        self.docs = []
        self.cur = -1
        self._switching = False          # 切标签时防止信号回环
        self._dirty_error = ""
        self.editor = EditorAdapter()
        self.worker = None
        self.run_worker = None
        self.ai_worker = None                # AI 修改的后台线程
        self._ai_fix_pending = False
        self._ai_fix_mode = "inplace"
        self._fixed_win = None
        self._explanation_html = ""          # 最近一次的讲解（供「帮助」关闭后恢复）
        self._help_win = None                # 使用说明独立窗口（懒创建）
        self._zoom = 0                       # 相对默认字号的缩放步数
        self._restoring = True               # 恢复会话期间不要回写会话
        self._build_ui()
        # 接着上次写：恢复上次打开的标签（含未保存的改动）；没有就展示示例
        if not self._restore_session():
            self._load_sample()
        self._restoring = False
        # 定时比对"编辑器里的文本 vs 文档模型里的文本"，用来给标签加 * 号。
        # 不挂在 Monaco 的 onDidChangeModelContent 上：切标签时 set_text 也会触发它，
        # 结果一切换就变成"已修改"——用文本比对最笨也最准。
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setInterval(1200)
        self._dirty_timer.timeout.connect(self._poll_dirty)
        self._dirty_timer.start()
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
        # 右键菜单里的「锚点」命令：qtmonaco 的扩展点在页面就绪后才可用，
        # 它内部会把消息缓存到初始化完成，所以这里早一点挂也没关系。
        if self.editor.monaco:
            try:
                self.editor.widget.context_menu_action_triggered.connect(
                    self._on_editor_action)
            except Exception:                                   # noqa: BLE001
                pass
        self._install_editor_actions()
        QTimer.singleShot(1200, self._install_editor_actions)
        QTimer.singleShot(2500, self._sync_anchors)   # 慢机器上再补一次
        # 右键菜单汉化的最后一道保险：网页菜单由注入脚本自己轮询，
        # 这里再兜住"万一弹的是 Qt 原生菜单"的情况，并留一份现场快照。
        self._ctx_dump_done = False
        self._ctx_js_ticks = 0
        self._ctx_timer = QTimer(self)
        self._ctx_timer.setInterval(700)
        self._ctx_timer.timeout.connect(self._ctx_menu_watch)
        self._ctx_timer.start()
        # 第一次使用：自动弹一次使用说明；之后不再打扰（记在用户配置里）
        try:
            if not bool((config_mod.load().get("ui") or {}).get("help_shown")):
                QTimer.singleShot(900, self._show_help_first_time)
        except Exception:                                       # noqa: BLE001
            pass
        # 后台预热编译器环境：把 MSVC 那 1.8 秒的 vcvars 初始化挪到启动时，
        # 学生第一次按 F5 就不必等它（之后每次检查本来只要几十毫秒）。
        _start_compiler_warmup()

    # ---------- 当前文档（保留 self.path / self.encoding 的旧写法） ----------
    def _doc(self):
        if 0 <= self.cur < len(self.docs):
            return self.docs[self.cur]
        return None

    @property
    def path(self):
        d = self._doc()
        return d.path if d else None

    @path.setter
    def path(self, value):
        d = self._doc()
        if d is not None:
            d.path = value

    @property
    def encoding(self):
        d = self._doc()
        return d.encoding if d else "mbcs"

    @encoding.setter
    def encoding(self, value):
        d = self._doc()
        if d is not None:
            d.encoding = value

    # ---------- 多文档管理 ----------
    def _sync_tabs(self):
        """把文档列表同步到标签栏（标题、提示、当前页）。"""
        self._switching = True
        try:
            self.tabbar.blockSignals(True)
            if self.tabbar.count() != len(self.docs):
                while self.tabbar.count():
                    self.tabbar.removeTab(0)
                for d in self.docs:
                    self.tabbar.addTab(d.title())
            for i, d in enumerate(self.docs):
                if i < self.tabbar.count() and self.tabbar.tabText(i) != d.title():
                    self.tabbar.setTabText(i, d.title())
                if i < self.tabbar.count():
                    self.tabbar.setTabToolTip(i, d.tooltip())
            if 0 <= self.cur < self.tabbar.count():
                self.tabbar.setCurrentIndex(self.cur)
            self.tabbar.blockSignals(False)
            self._update_file_label()
            self._sync_compare_button()
        finally:
            self._switching = False

    def _update_file_label(self):
        d = self._doc()
        if d is None:
            self.lbl_file.setText("")
            return
        if d.path:
            where = d.path
        else:
            where = "还没保存到文件"
        if d.modified:
            where += "　● 未保存"
        self.lbl_file.setText("%s　（%s）" % (where, encoding_label(d.encoding)))

    def _flush(self):
        """把编辑器里的内容存回当前文档模型，并更新改动标记。"""
        d = self._doc()
        if d is None:
            return
        try:
            txt = _norm_text(self.editor.text())
        except Exception:                                       # noqa: BLE001
            return
        d.text = txt
        d.modified = (txt != d.saved_text)
        # 顺带把"程序输入"存回这个文档，切走/关闭后再回来还在
        try:
            d.input_text = self.txt_input.toPlainText()
        except Exception:                                       # noqa: BLE001
            pass

    def _poll_dirty(self):
        """定时检查有没有改动，给标签加 * 号。

        用"文本和上次保存的内容比对"，而不是挂 Monaco 的 onDidChangeModelContent——
        因为切标签时的 set_text 也会触发那个事件，结果一切换就变成"已修改"。
        """
        if self._switching or self.cur < 0:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        if self.run_worker is not None and self.run_worker.isRunning():
            return
        d = self._doc()
        if d is None:
            return
        try:
            txt = _norm_text(self.editor.text())
        except Exception:                                       # noqa: BLE001
            return
        if txt == d.text:
            return
        d.text = txt
        was = d.modified
        d.modified = (txt != d.saved_text)
        if d.modified != was:
            self._sync_tabs()

    def _push_current_to_editor(self, d, remember_marks=True):
        """把一个文档的内容灌进编辑器。"""
        self._switching = True
        try:
            self.editor.set_text(d.text)
            self.editor.clear_marks()
            if remember_marks and d.marks:
                self.editor.mark_errors(d.marks)
            # 锚点跟着标签走：切过来就把这个文档的锚点画上（只画，不动文字）
            self.editor.set_anchors(list(d.anchors))
            # 程序输入框也跟着文档走：每个项目各记各的，切过来只看到自己的数据
            self.txt_input.setPlainText(d.input_text or "")
            # 没改动过的文档：把基线校正成编辑器实际返回的文本。
            # 编辑器可能对内容做归一化（比如换行符），不校正的话一打开就是"已修改"。
            if not d.modified:
                txt = _norm_text(self.editor.text())
                if txt:
                    d.text = txt
                    d.saved_text = txt
            for i, (_, code) in enumerate(ENCODINGS):
                if code == d.encoding:
                    self.cmb_enc.blockSignals(True)
                    self.cmb_enc.setCurrentIndex(i)
                    self.cmb_enc.blockSignals(False)
                    break
        finally:
            self._switching = False

    def _activate(self, index: int):
        """切换到第 index 个文档。"""
        if not (0 <= index < len(self.docs)) or index == self.cur:
            self._sync_tabs()
            return
        self._flush()                    # 先保存当前这个
        self.cur = index
        d = self.docs[index]
        self._push_current_to_editor(d)
        self._last_report = None         # 换了文件，上一份讲解别串过来
        self._explanation_html = self._empty_html()
        self._apply_view()
        self._sync_tabs()
        self._save_session()             # 切过标签就记一下，程序异常退出也不至于丢

    def _open_doc(self, doc: "Doc", activate=True):
        """新开一个文档标签。已经打开过的文件直接跳过去，不重复开。"""
        if doc.path:
            target = os.path.normcase(os.path.abspath(doc.path))
            for i, d in enumerate(self.docs):
                if d.path and os.path.normcase(os.path.abspath(d.path)) == target:
                    if activate:
                        self._activate(i)
                    return
        self._flush()
        self.docs.append(doc)
        if activate:
            self.cur = len(self.docs) - 1
            self._push_current_to_editor(doc)
            self._last_report = None
            self._explanation_html = self._empty_html()
            self._apply_view()
        self._sync_tabs()
        self._save_session()

    def _close_doc(self, index: int):
        """关掉一个标签；有未保存改动就先问。"""
        if not (0 <= index < len(self.docs)):
            return
        if index == self.cur:
            self._flush()
        d = self.docs[index]
        if not self._confirm_discard(d):
            return
        del self.docs[index]
        if not self.docs:
            self.cur = -1
            self._load_sample()          # 全关了就给一个示例，不留空白界面
            return
        # 关掉的是当前标签左侧的标签时，当前文档的位置要**左移一位**。
        # 以前这里只做 min(cur, len-1) 的钳制，没有减一：开着 A、B、C 而当前在 B，
        # 关掉 A 之后界面会直接跳到 C —— 学生以为自己误删了东西。
        if index < self.cur:
            self.cur -= 1
        elif self.cur >= len(self.docs):
            self.cur = len(self.docs) - 1
        self._push_current_to_editor(self.docs[self.cur])
        self._sync_tabs()
        self._save_session()

    def _confirm_discard(self, d) -> bool:
        """有未保存改动时询问单个文档。返回 True 表示"可以继续"。"""
        if not d.modified:
            return True
        return self._confirm_close_all([d], title="还没保存", ask_cancel=True) != "cancel"

    def _confirm_close_all(self, docs, title="还没保存", ask_cancel=True) -> str:
        """一次问完所有未保存的文档，而不是每个弹一个框。

        为什么改：以前是"每个文件一个模态框"。同时开 4 个文件、每个都改过，
        关程序就要连点 4 次；中间手一抖点了「取消」，整个关闭被中止，
        用户以为程序卡住了或者丢了东西，往往直接强制结束——**那才会真的丢**。

        现在只弹一次，列出所有未保存的文件，给三个选择：
        全部保存 / 全部不保存 / 取消。
        """
        todo = [d for d in docs if d.modified]
        if not todo:
            return "save"
        names = [d.title().lstrip("* ") for d in todo]
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        if len(todo) == 1:
            box.setText("《%s》有改动还没保存。" % names[0])
            box.setInformativeText("要先保存吗？不保存的话这些改动只会留在「上次进度」里。")
        else:
            box.setText("有 %d 个文件改了还没保存：" % len(todo))
            shown = names[:8]
            more = "" if len(names) <= 8 else "\n……还有 %d 个" % (len(names) - 8)
            box.setInformativeText("\n".join("· " + n for n in shown) + more +
                                   "\n\n不保存的话，改动只会留在「上次进度」里，"
                                   "下次打开还能接着改。")
        b_save = box.addButton("全部保存" if len(todo) > 1 else "保存",
                               QMessageBox.AcceptRole)
        b_drop = box.addButton("全部不保存" if len(todo) > 1 else "不保存",
                               QMessageBox.DestructiveRole)
        b_cancel = box.addButton("取消", QMessageBox.RejectRole) if ask_cancel else None
        box.setDefaultButton(b_save)
        box.exec()
        clicked = box.clickedButton()
        if b_cancel is not None and clicked is b_cancel:
            return "cancel"
        return "drop" if (b_drop is not None and clicked is b_drop) else "save"

    def _save_doc(self, d) -> bool:
        """保存指定文档。返回是否成功（取消也算失败，用来中止关闭流程）。"""
        target = d.path
        if not target:
            suggested = os.path.join(self._projects_dir(), "untitled.c")
            target, _ = QFileDialog.getSaveFileName(self, "保存为", suggested, "C 源文件 (*.c)")
            if not target:
                return False
        try:
            with open(target, "w", encoding=d.encoding, newline="\n") as f:
                f.write(d.text)
        except (OSError, LookupError) as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return False
        d.path = target
        d.saved_text = d.text
        d.modified = False
        self._sync_tabs()
        return True

    def _save_all(self) -> bool:
        """保存所有有改动的文档。任一失败返回 False。"""
        ok = True
        for d in list(self.docs):
            if d is self._doc():
                self._flush()
            if d.modified and not self._save_doc(d):
                ok = False
        return ok

    def closeEvent(self, event):
        """退出前问一次未保存的改动，然后把**全部**打开的文件记下来。

        顺序很关键：**先把会话写下去，再弹询问框**。
        因为用户可能在询问框上点「取消」、或者干脆强制结束进程；
        先存的话，无论后面发生什么，"上次进度"都是完整的，
        下次打开仍然是所有文件都在。
        """
        if self._doc() is not None:
            self._flush()
        self._save_session()             # 先落盘，再问
        choice = self._confirm_close_all(list(self.docs), title="退出前确认")
        if choice == "cancel":
            event.ignore()
            return
        if choice == "save":
            if not self._save_all():
                event.ignore()           # 有文件存不下来（比如被占用），别静默退出
                return
            self._save_session()         # 保存后改动已清零，刷新一次会话
        try:
            self._dirty_timer.stop()
            self._ctx_timer.stop()       # 关窗口后别再往已释放的页面上跑 JS
        except Exception:                                       # noqa: BLE001
            pass
        event.accept()

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
        # 编辑器可能被替换成没有补全能力的实现（测试替身 / 降级编辑器），
        # 这里不能假定方法一定存在，否则定时器回调会抛异常刷一屏红字。
        probe = getattr(self.editor, "probe_completion", None)
        if probe is None:
            return
        try:
            probe(done)
        except Exception:                                       # noqa: BLE001
            pass

    # ---------- 界面 ----------
    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        # 紧凑排版：边距和间距都压到最小，省下的纵向空间全部让给代码区
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        # 工具栏只留"每次都要点"的按钮。
        # 打开/帮助等命令只保留菜单一个入口 —— 之前两处都有，是重复的。
        self.btn_new = QPushButton("新建文件")
        self.btn_new.setToolTip("新建一个 .c 文件并选择存放位置（Ctrl+N）")
        self.btn_save = QPushButton("保存")
        self.btn_save.setToolTip("保存当前文件（Ctrl+S）")
        self.btn_align = QPushButton("对齐")
        self.btn_align.setToolTip(
            "把缩进整理规范（Ctrl+Shift+F）。\n"
            "只改行首缩进和行尾空格，不会动你的代码内容；\n"
            "按 Ctrl+Shift+Z 可以撤回到对齐前。")
        self.btn_ai_fix = QPushButton("AI 修改")
        self.btn_ai_fix.setToolTip(
            "让 AI 给一份能编译通过的修正代码（Ctrl+Shift+A）。\n"
            "会先问你用哪种方式对照：原文里改 / 另开窗口 / 只在行下加注释。\n"
            "改动只进编辑器，不会自动保存到你的文件。")
        self.btn_compare = QPushButton("对照")
        self.btn_compare.setToolTip("和 AI 修改之前的版本逐行对照")
        self.btn_compare.setEnabled(False)
        # 锚点：书签列表。添加锚点在右键菜单里（右键 → 添加锚点…），
        # 这里只负责"列出全部 + 跳转"，因为跳转是最高频的动作。
        self.btn_anchor = QPushButton("锚点")
        self.btn_anchor.setToolTip(
            "列出所有锚点，双击跳转。\n"
            "添加：在代码行上右键 → 添加锚点…（也可以删除本行锚点）\n"
            "锚点只是书签，不会改动你的代码，也不会让文件变成「已修改」。")
        self.btn_anchor.clicked.connect(self.show_anchors)
        self.btn_check = QPushButton("检查 (F5)")
        self.btn_check.setDefault(True)
        self.btn_run = QPushButton("运行 (F9)")
        self.btn_run.setToolTip(
            "编译并运行，输出直接显示在下面的「运行输出」页，不会弹黑色命令行窗口。\n"
            "程序要输入（scanf）时，先把文字填进上方输入框再运行。")
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(1, 60)
        self.spin_timeout.setValue(10)
        self.spin_timeout.setSuffix(" 秒")
        self.spin_timeout.setToolTip(
            "程序连续计算多久算超时（防止死循环卡住）。\n"
            "等你在输入框里敲数据的时间不算在内——想多久都行。")
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

        # 工具栏紧凑化：按钮/输入框统一压到一行文字 + 6px 的高度。
        # 高度按字体度量算，而不是写死像素 —— 系统缩放到 125%/150% 时不会把字切掉。
        _h = self.fontMetrics().height() + 6
        _css = ("QPushButton{padding:0 9px;}"
                "QSpinBox,QComboBox{padding:0 4px;}"
                "QCheckBox{spacing:3px;}")
        for _w in (self.btn_new, self.btn_save, self.btn_align, self.btn_ai_fix,
                   self.btn_compare, self.btn_anchor, self.btn_check, self.btn_run,
                   self.spin_timeout, self.cmb_depth, self.chk_ai, self.cmb_enc):
            _w.setStyleSheet(_css)
            _w.setFixedHeight(_h)

        bar.addWidget(self.btn_new)
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_align)
        bar.addWidget(self.btn_ai_fix)
        bar.addWidget(self.btn_compare)
        bar.addWidget(self.btn_anchor)
        bar.addSpacing(8)
        bar.addWidget(self.btn_check)
        bar.addWidget(self.btn_run)
        bar.addWidget(self.spin_timeout)
        bar.addStretch(1)
        bar.addWidget(self.cmb_depth)
        bar.addWidget(self.chk_ai)
        bar.addSpacing(8)
        bar.addWidget(QLabel("编码："))
        bar.addWidget(self.cmb_enc)
        layout.addLayout(bar)

        # 标签栏：一个标签 = 一个打开的文件（可以来自不同项目）
        self.tabbar = QTabBar()
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.setElideMode(Qt.ElideRight)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setToolTip("点标签切换文件；× 关闭当前标签")
        # 标签本身也压矮：默认标签高度会吃掉将近一行代码的纵向空间
        self.tabbar.setStyleSheet(
            "QTabBar::tab{padding:2px 9px;margin:0 1px 0 0;}")
        self.tabbar.currentChanged.connect(self._on_tab_changed)
        self.tabbar.tabCloseRequested.connect(self._close_doc)

        # 文件路径和标签栏并排一行：路径不再单独占一行高度。
        # 用 Ignored 策略，标签多的时候路径自动让位截断，不会把标签挤出去。
        self.lbl_file = QLabel("")
        self.lbl_file.setStyleSheet("color:#6B7280;font-size:8pt;")
        self.lbl_file.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_file.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tabrow = QHBoxLayout()
        tabrow.setContentsMargins(0, 0, 0, 0)
        tabrow.setSpacing(8)
        tabrow.addWidget(self.tabbar, 0)
        tabrow.addWidget(self.lbl_file, 1)
        layout.addLayout(tabrow)

        splitter = QSplitter(Qt.Vertical)
        # 分割条的"抓握区"要够大：默认只有 4px 的细线，鼠标不精确停到那条线上根本
        # 拖不动——尤其下方面板是「运行输出」时，那条线紧贴输入框的上边框，
        # 学生来回来找"交界点"。这里把整条加宽到 26px（约一个半鼠标箭头的高度），
        # 下面那段正好盖住页签上方的浅灰留白，看上去就是一条浅灰分隔带，
        # 悬停/按下再加深，位置一眼可见、一按就中。
        splitter.setHandleWidth(30)
        splitter.setStyleSheet("""
            QSplitter::handle:vertical {
                background: #f2f4f7;
                border-top: 2px solid #b9c4d1;
            }
            QSplitter::handle:vertical:hover  { background: #e3eaf2; }
            QSplitter::handle:vertical:pressed{ background: #d2dfee; }
        """)
        self.splitter = splitter
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
            "程序用到 scanf / gets 时：先填好再按 F9（一次性喂给它）；"
            "运行中也在这里输入，回车发送——程序问一句、你答一句")
        self.txt_input.setFixedHeight(44)
        self.txt_input.setFont(QFont("Consolas", 10))
        inrow.addWidget(self.txt_input, 1)
        self.btn_send_input = QPushButton("发送 (Enter)")
        self.btn_send_input.setToolTip(
            "程序运行中：把输入框里的这一行送进程序（回车也行）。\n"
            "程序没在运行时这个按钮不亮——先按 F9。")
        self.btn_send_input.setEnabled(False)
        self.btn_send_input.clicked.connect(self._send_input)
        inrow.addWidget(self.btn_send_input)
        rl.addLayout(inrow)
        self.view_run = QTextBrowser()
        self.view_run.setOpenExternalLinks(True)
        # 运行输出按等宽字体显示：程序输出里的列对齐、缩进才有意义；
        # 实时追加用的是纯文本，字体得在控件上设定。
        self.view_run.setFont(QFont("Consolas", 10))
        self.view_run.setStyleSheet(
            "QTextBrowser{background:#FFFFFF;border:1px solid #dde3ea;}")
        rl.addWidget(self.view_run, 1)
        self.tabs.addTab(run_page, "运行输出")
        # 「结束输入」放在页签栏右角，紧挨着「运行输出」：程序卡在"等输入到 EOF"
        # 的时候（while (scanf(...) != EOF) 这类），学生盯着输出页就能顺手点它，
        # 不用先回输入框。输入框空着按回车等同于点它。
        self.btn_end_input = QPushButton("结束输入 (EOF)")
        self.btn_end_input.setToolTip(
            "告诉正在运行的程序：没有更多输入了（等价于终端里按 Ctrl+Z / Ctrl+D）。\n"
            "程序读到 EOF 就会继续往下走或退出。\n"
            "输入框空着按回车，效果相同。")
        self.btn_end_input.setEnabled(False)
        self.btn_end_input.clicked.connect(self._end_input)
        # 「停止」：等输入的时间不算超时（见 engine/compilers.py 的 _wait_done），
        # 好处是学生想多久都不会被杀，代价是"真的停不下来"时得有个手动出口。
        self.btn_stop_run = QPushButton("停止")
        self.btn_stop_run.setToolTip(
            "立刻终止正在运行的程序（连它开的子进程一起杀）。\n"
            "注意：等你输入的时间本来就不算超时，所以一般用不着它——\n"
            "程序真的卡在死循环里出不来时才点。")
        self.btn_stop_run.setEnabled(False)
        self.btn_stop_run.clicked.connect(self._stop_run)
        _corner = QWidget()
        _cl = QHBoxLayout(_corner)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cl.setSpacing(6)
        _cl.addWidget(self.btn_end_input)
        _cl.addWidget(self.btn_stop_run)
        self.tabs.setCornerWidget(_corner, Qt.TopRightCorner)
        # 讲解栏和运行输出都装上平滑滚动（滚轮变成短动画，读长内容不跳）
        self._scrollers = [SmoothScroller(self.view), SmoothScroller(self.view_run)]
        self._run_watch = QTimer(self)
        self._run_watch.setInterval(500)
        self._run_watch.timeout.connect(self._tick_run_watch)
        self._last_chunk_at = 0.0
        self._idle_hint_shown = True
        self._last_html = None
        # 缩放的事件过滤器挂在 QApplication 上（见 __init__），
        # 这样无论焦点在编辑器还是输出区都能生效。
        splitter.addWidget(self.tabs)
        # 代码区优先：默认就把大头给编辑器，窗口变大时多出来的空高也主要给它。
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        self.tabs.setMinimumHeight(110)
        splitter.setSizes([640, 190])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.status = self.statusBar()
        self.status.showMessage(self.editor.note)

        act_new = QAction("新建文件…", self)
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
        act_align = QAction("对齐（整理缩进）", self)
        act_align.setShortcut("Ctrl+Shift+F")
        act_align.triggered.connect(self.format_code)
        act_align_undo = QAction("撤回到对齐前", self)
        act_align_undo.setShortcut("Ctrl+Shift+Z")
        act_align_undo.triggered.connect(self.undo_format)
        act_ai_fix = QAction("AI 修改代码…", self)
        act_ai_fix.setShortcut("Ctrl+Shift+A")
        act_ai_fix.triggered.connect(self.do_ai_fix)
        act_compare = QAction("和 AI 修改前对照", self)
        act_compare.setShortcut("Ctrl+Shift+D")
        act_compare.triggered.connect(self.show_ai_compare)
        act_close = QAction("关闭当前标签", self)
        act_close.setShortcut("Ctrl+W")
        act_close.triggered.connect(self.close_current)
        act_sample = QAction("新建示例标签", self)
        act_sample.triggered.connect(self._load_sample)
        act_where = QAction("设置项目存放位置…", self)
        act_where.triggered.connect(self.set_projects_dir)
        act_check = QAction("检查", self)
        act_check.setShortcut("F5")
        act_check.triggered.connect(self.do_check)
        act_run = QAction("运行（输出显示在下方，不弹黑框）", self)
        act_run.setShortcut("F9")
        act_run.triggered.connect(self.do_run)
        self.btn_run.clicked.connect(self.do_run)
        self.btn_new.clicked.connect(self.new_project)
        self.btn_save.clicked.connect(self.save_file)
        self.btn_align.clicked.connect(self.format_code)
        self.btn_ai_fix.clicked.connect(self.do_ai_fix)
        self.btn_compare.clicked.connect(self.show_ai_compare)
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
        m.addAction(act_sample)
        m.addSeparator()
        m.addAction(act_save)
        m.addAction(act_save_as)
        m.addSeparator()
        m.addAction(act_align)
        m.addAction(act_align_undo)
        m.addSeparator()
        m.addAction(act_ai_fix)
        m.addAction(act_compare)
        m.addSeparator()
        m.addAction(act_close)
        m.addAction(act_where)
        m.addSeparator()
        m.addAction("退出", self.close)

        # 「检查」菜单改名「运行」：它本来就同时管检查与运行，而且缩放不该塞在这里
        m2 = self.menuBar().addMenu("运行")
        m2.addAction(act_check)
        m2.addAction(act_run)

        m_view = self.menuBar().addMenu("视图")
        m_view.addAction(act_zoom_in)
        m_view.addAction(act_zoom_out)
        m_view.addAction(act_zoom_reset)

        m3 = self.menuBar().addMenu("帮助")
        # 「使用说明」现在打开独立窗口，不再占用讲解栏
        self.act_help = QAction("使用说明", self)
        self.act_help.setCheckable(True)
        self.act_help.setChecked(False)
        self.act_help.setShortcut("F1")
        self.act_help.triggered.connect(self.toggle_help)
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
            '<p><b>这是一个「帮你看懂编译报错」的工具。</b>'
            '默认只讲解、不替你改代码；想要改好的版本，得自己点「AI 修改」。</p>'
            '<p>用法：把 C 代码贴进上面的编辑区，按 <b>F5</b> 或点「检查」。</p>'
            '<ul>'
            '<li><b>输入前几个字母就会有候选</b>——例如打 <code>pri</code> 选 printf，'
            '打 <code>for</code> 直接补出完整循环，按 Tab 或回车选中，减少打字与拼写错误</li>'
            '<li>只讲<b>最可能相关的一条</b>错误，避免几十条报错一次糊上来</li>'
            '<li>解释分 5 层：结论 → 定位 → 原因 → 改法 → 知识点</li>'
            '<li>本地知识库优先，命中时<b>不联网、不花钱</b></li>'
            '<li>行号与位置<b>只来自编译器</b>，不由模型编造</li>'
            '</ul>'
            '<p><b>同时开好几个文件：</b>打开或新建的文件都会出现在上面的标签栏里，'
            '点标签切换、点 × 关闭（<b>Ctrl+W</b>）。可以一边开着作业、一边开着练习。</p>'
            '<p><b>关掉程序也不会丢：</b>下次打开会<b>自动回到上次那几个文件和那个标签</b>，'
            '连你<b>没保存的改动</b>都还在（标签上带 * 号，提醒你还没存盘）。</p>'
            '<p><b>想让 AI 直接给出改好的代码？</b>点工具栏 <b>「AI 修改」</b>'
            '（<b>Ctrl+Shift+A</b>）。它会先问你想怎么看改了什么，有三种：</p>'
            '<ul>'
            '<li><b>原位修改</b>：直接改在编辑器里，改完点 <b>「对照」</b> 比一比</li>'
            '<li><b>独立窗口</b>：另开一个窗口放正确代码，可以拖开并排对照，原文件不动</li>'
            '<li><b>行内注释</b>：不动代码，只在出问题那一行下面加一行 <code>// AI 建议</code></li>'
            '</ul>'
            '<p style="color:#6B7280;font-size:10pt;">AI 改完<b>不会自动保存</b>，'
            '标签会变成未保存状态；按 <b>Ctrl+Shift+Z</b> 随时可以还原。</p>'
            '<p><b>缩进乱了？</b>点工具栏 <b>「对齐」</b>（<b>Ctrl+Shift+F</b>）会把缩进整理规范。'
            '它<b>只改行首缩进和行尾空格，不动你的代码内容</b>；'
            '整理完按 <b>Ctrl+Shift+Z</b> 可以退回去。</p>'
            '<p><b>运行怎么用：</b>按 <b>F9</b> 直接编译并运行，输出显示在下方'
            '「运行输出」页——<b>不会弹出黑色命令行窗口</b>。'
            '程序要输入（<code>scanf</code>）时，先把要输入的文字填进工具栏上方的'
            '输入框再运行；超过设定秒数会自动终止（防止死循环）。</p>'
            '<p><b>常用头文件：</b>打 <code>inc</code> 会出现 <code>include</code> 片段，'
            '下拉里可以选 stdio / stdlib / string / math / <b>time</b> / ctype；'
            '这些库的常用函数（<code>time</code>、<code>ctime</code>、'
            '<code>isalpha</code>、<code>toupper</code>、<code>memset</code> 等）'
            '也都做成了补全候选，选中就有中文说明。</p>'
            '<p><b>字号太小看不清？</b>按 <b>Ctrl+=</b> 放大、<b>Ctrl+-</b> 缩小、'
            '<b>Ctrl+0</b> 恢复默认；也可以按住 <b>Ctrl</b> 滚滚轮。字号会记住。</p>'
            '<p style="color:#6B7280;font-size:10pt;">检查和运行用的都是工作副本，'
            '不会动你的源文件。<br>'
            '有没保存的改动时，关标签和退出都会先问你一句（一次问完所有文件）。<br>'
            '这个窗口再点一次「使用说明」或按 <b>F1</b> 就会关掉。</p></div>')

    def _empty_html(self) -> str:
        return (
            '<div style="font-family:\'Microsoft YaHei\',sans-serif;font-size:11pt;'
            'color:#6B7280;padding:16px;line-height:1.9;">'
            '按 <b>F5</b> 或点工具栏的「检查」，讲解会显示在这里。<br>'
            '按 <b>F9</b> 直接运行，结果显示在「运行输出」页（不弹黑框）。<br>'
            '<span style="font-size:10pt;">想再看一遍使用说明：按 <b>F1</b>，'
            '或菜单「帮助 → 使用说明」。</span>'
            '</div>')

    def _apply_view(self, keep_scroll: bool = False):
        """讲解栏只显示讲解。使用说明已经搬到独立窗口（见 toggle_help）。

        「丝滑」体现在两个细节：
          · 内容没变就不重设 HTML —— 重设会让滚动条瞬间归零，看着"闪一下"；
          · 改讲深层数这类**内容换但位置该保留**的场景，记住原来的滚动位置。
        """
        html_text = self._explanation_html or self._empty_html()
        if html_text == getattr(self, "_last_html", None):
            return
        self._last_html = html_text
        sb = self.view.verticalScrollBar()
        pos = sb.value() if keep_scroll else 0
        for sc in getattr(self, "_scrollers", []):
            sc.reset()
        self.view.setHtml(html_text)
        if keep_scroll and pos > 0:
            sb.setValue(min(pos, sb.maximum()))

    # ---------- 使用说明（独立窗口） ----------
    def show_help(self):
        """打开说明窗口（已经开着就置前，**不会关掉它**）。

        和 toggle_help 分开是有原因的：第一次启动时的自动弹出用这个。
        如果那里用 toggle，而用户恰好在那一瞬间自己点开了说明，
        定时器一到就会把刚打开的窗口关掉——用户会觉得"点了没反应"。
        """
        if self._help_win is None:
            self._help_win = HelpDialog(self, self._help_html())
            self._help_win.finished.connect(self._on_help_closed)
        self._help_win.show()
        self._help_win.raise_()
        self._help_win.activateWindow()
        self.act_help.setChecked(True)

    def toggle_help(self):
        """点一下打开说明窗口，再点一下关掉；F1 同样。"""
        if self._help_win is not None and self._help_win.isVisible():
            self._help_win.close()
            return
        self.show_help()

    def _on_help_closed(self, *_args):
        self.act_help.setChecked(False)

    def _show_help_first_time(self):
        """第一次使用自动弹出说明，之后不再打扰。"""
        self.show_help()
        try:
            config_mod.save_user_config({"ui": {"help_shown": True}})
        except Exception:                                       # noqa: BLE001
            pass

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
        """载入示例作为**新的标签**（而不是覆盖当前文件）。"""
        doc = Doc(path=None, encoding="mbcs", text=SAMPLE,
                  label="示例（含全角分号错误）")
        self._open_doc(doc)

    def _on_tab_changed(self, index: int):
        if self._switching or index < 0:
            return
        self._activate(index)

    def open_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "打开 C 源文件", self._projects_dir(),
            "C 源文件 (*.c *.h);;所有文件 (*.*)")
        for p in paths or []:
            self._open_path(p)

    def save_file(self, force_dialog: bool = False):
        """保存当前文档。force_dialog=True 时强制弹「另存为」。"""
        d = self._doc()
        if d is None:
            return
        self._flush()
        if force_dialog:
            old = d.path
            d.path = None
            if not self._save_doc(d):
                d.path = old
                return
        elif not self._save_doc(d):
            return
        self.status.showMessage("已保存：%s" % (d.path or ""), 4000)

    def close_current(self):
        if self.cur >= 0:
            self._close_doc(self.cur)

    # ---------- 锚点（书签） ----------
    # 右键菜单里的命令。id 必须唯一，label 直接显示在菜单上。
    # 菜单本身是网页里自己画的（全中文），这些命令挂在编辑器上，点了回到这里处理。
    CTX_ACTIONS = (
        ("ctutor_cut", "剪切"),
        ("ctutor_copy", "复制"),
        ("ctutor_paste", "粘贴"),
        ("ctutor_delete", "删除"),
        ("ctutor_selectall", "全选"),
        ("ctutor_anchor_add", "添加锚点…"),
        ("ctutor_anchor_list", "锚点列表…"),
        ("ctutor_anchor_del", "删除本行锚点"),
        ("ctutor_undo", "撤销"),
        ("ctutor_redo", "重做"),
    )

    def _install_editor_actions(self):
        """把锚点命令挂到编辑器右键菜单（只挂一次）。"""
        if getattr(self, "_editor_actions_done", False) or not self.editor.monaco:
            return
        done = 0
        for aid, label in self.CTX_ACTIONS:
            if self.editor.add_context_action(aid, label):
                done += 1
        self._editor_actions_done = done == len(self.CTX_ACTIONS)
        # 页面就绪后补画一次锚点：恢复上次进度是在窗口构造时做的，
        # 那时候 Monaco 还没加载完，early 的那次 set_anchors 会被丢掉。
        self._sync_anchors()

    def _on_editor_action(self, action_id):
        """右键菜单里的命令被点了。"""
        if action_id == "ctutor_anchor_add":
            self.editor.cursor_info(self._anchor_add_from_cursor)
        elif action_id == "ctutor_anchor_list":
            self.show_anchors()
        elif action_id == "ctutor_anchor_del":
            self.editor.cursor_info(self._anchor_del_from_cursor)
        elif action_id in ("ctutor_cut", "ctutor_copy"):
            self._editor_copy(cut=(action_id == "ctutor_cut"))
        elif action_id == "ctutor_paste":
            self._editor_paste()
        elif action_id == "ctutor_delete":
            self.editor.eval_js("window.__ctutor_do('delete')")
        elif action_id == "ctutor_selectall":
            self.editor.eval_js("window.__ctutor_do('selectall')")
        elif action_id in ("ctutor_undo", "ctutor_redo"):
            self.editor.eval_js("window.__ctutor_do('%s')" % action_id.split("_", 1)[1])

    def _editor_copy(self, cut=False):
        """复制/剪切：文字从编辑器取出来，放进系统剪贴板。"""
        def _got(raw):
            try:
                text = json.loads(raw) if raw else ""
            except (ValueError, TypeError):
                text = ""
            if not text:
                return
            QApplication.clipboard().setText(text)
            if cut:
                self.editor.eval_js("window.__ctutor_do('delete')")
        self.editor.eval_js("window.__ctutor_do('seltext')", _got)

    def _editor_paste(self):
        """粘贴：系统剪贴板里的文字插到光标/选区处。"""
        text = QApplication.clipboard().text() or ""
        if not text:
            return
        self.editor.eval_js("window.__ctutor_do('insert', %s)" % json.dumps(text))

    def _anchor_add_from_cursor(self, raw):
        try:
            info = json.loads(raw or "{}")
        except ValueError:
            info = {}
        self.add_anchor(int(info.get("line") or 1), info.get("text") or "", ask=True)

    def add_anchor(self, line, text="", name=None, ask=False):
        """加一个锚点。ask=True 时弹框问名字（右键入口走这条）。

        注意：这里**不碰编辑器里的文本**，也不把文档标成已修改 ——
        锚点是书签，不是代码。它只影响显示，以及存在会话文件里跨重启保留。
        """
        d = self._doc()
        if d is None:
            return None
        line = max(1, int(line))
        if ask or name is None:
            default = (text or "").strip()[:24] or ("第 %d 行" % line)
            name, ok = QInputDialog.getText(
                self, "添加锚点", "给这个锚点起个名字（跳转时靠它认路）：", text=default)
            if not ok:
                return None
        name = (name or "").strip() or ("第 %d 行" % line)
        snippet = (text or "").strip()[:80]
        for a in d.anchors:
            if int(a.get("line") or 0) == line:          # 同一行只留一个
                a["name"], a["text"] = name, snippet
                break
        else:
            d.anchors.append({"name": name, "line": line, "text": snippet})
        d.anchors.sort(key=lambda a: int(a.get("line") or 0))
        self._sync_anchors()
        self._save_session()                  # 锚点不进 .c 文件，但下次打开要还在
        self.status.showMessage("已加锚点「%s」（第 %d 行）" % (name, line), 4000)
        return name

    def _anchor_del_from_cursor(self, raw):
        try:
            info = json.loads(raw or "{}")
        except ValueError:
            info = {}
        n = self.remove_anchors_at(int(info.get("line") or 1))
        self.status.showMessage(
            ("已删除 %d 个锚点" % n) if n else "这一行没有锚点", 4000)

    def remove_anchors_at(self, line):
        d = self._doc()
        if d is None:
            return 0
        before = len(d.anchors)
        d.anchors = [a for a in d.anchors if int(a.get("line") or 0) != int(line)]
        n = before - len(d.anchors)
        if n:
            self._sync_anchors()
            self._save_session()
        return n

    def anchors(self):
        """当前标签的锚点列表（只读副本，给对话框/测试用）。"""
        d = self._doc()
        return list(d.anchors) if d is not None else []

    def _sync_anchors(self):
        """把当前标签的锚点画到编辑器上（纯装饰，一个字符都不改）。"""
        try:
            self.editor.set_anchors(self.anchors())
        except Exception:                                       # noqa: BLE001
            pass

    def show_anchors(self):
        """锚点列表：跳转 / 重命名 / 删除。"""
        while True:
            items = self.anchors()
            dlg = AnchorDialog(items, self)
            dlg.exec()
            if dlg.action == "" or dlg.index < 0 or dlg.index >= len(items):
                return
            target = items[dlg.index]
            line = int(target.get("line") or 1)
            if dlg.action == "goto":
                self.editor.goto_anchor(line)
                self.status.showMessage("跳到「%s」（第 %d 行）" % (
                    target.get("name") or "锚点", line), 4000)
                return
            if dlg.action == "delete":
                doc = self._doc()
                if doc is not None:
                    doc.anchors = [a for a in doc.anchors if a is not target]
                    self._sync_anchors()
                    self._save_session()
            elif dlg.action == "rename":
                new, ok = QInputDialog.getText(
                    self, "重命名锚点", "新名字：", text=str(target.get("name") or ""))
                if ok and new.strip():
                    target["name"] = new.strip()
                    self._sync_anchors()
                    self._save_session()

    # ---------- 右键菜单汉化的兜底 ----------
    @staticmethod
    def ctx_menu_dump_path() -> str:
        return os.path.join(tempfile.gettempdir(), "ctutor-ctxmenu-dump.txt")

    def _ctx_menu_watch(self):
        """每 0.7 秒看一眼右键菜单。

        网页里的菜单由注入脚本翻译（它自己轮询），这里管两件它管不了的事：
          1. 万一弹的是 **Qt 原生菜单**（WebEngine 自己的），QAction 文案也换成中文；
          2. 把第一次见到的真实菜单存一份到临时目录，方便出问题时对着真结构改，
             而不是靠猜 DOM。
        """
        # 窗口已经关了/还没显示就别查了，否则会往已释放的页面上跑 JS
        try:
            if not self.isVisible():
                return
        except Exception:                                       # noqa: BLE001
            return
        # ---- 1. Qt 原生菜单（一直查：这一步很便宜）----
        try:
            pop = QApplication.activePopupWidget()
        except Exception:                                       # noqa: BLE001
            pop = None
        if pop is not None and hasattr(pop, "actions"):
            n = 0
            texts = []
            for act in pop.actions():
                try:
                    texts.append(act.text())
                except Exception:                               # noqa: BLE001
                    continue
                raw = (act.text() or "").replace("&", "").strip()
                zh = editorui_mod.MENU_ZH.get(raw)
                if zh and act.text() != zh:
                    act.setText(zh)
                    n += 1
            if n:
                self._ctx_dump_done = True
                self._write_ctx_dump("Qt 原生菜单", texts)
            return
        # ---- 2. 网页菜单的现场快照 ----
        # 这是排查用途：正常使用时不抓（每次启动要白跑 240 次页面 JS，
        # 只为存一份"菜单长什么样"的现场）。需要时设 CTUTOR_MENU_DEBUG=1 再启。
        if (not _MENU_DEBUG or self._ctx_dump_done
                or not self.editor.monaco or self._ctx_js_ticks > 240):
            return
        self._ctx_js_ticks += 1
        self.editor.eval_js(
            "JSON.stringify({html: window.__ctutor_menu_dump || '',"
            " labels: window.__ctutor_menu_labels || []})",
            self._ctx_menu_dump_ready)

    def _ctx_menu_dump_ready(self, raw):
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            return
        html_text = data.get("html") or ""
        if not html_text:
            return
        self._ctx_dump_done = True
        self._write_ctx_dump("网页菜单（.monaco-menu）", data.get("labels") or [],
                             html_text)

    def _write_ctx_dump(self, kind, labels, html_text=""):
        """把菜单现场写进临时目录（只写第一次，保留最有价值的那份）。

        这是给自己排查用的：万一还有没翻到的条目，这份 HTML 就是真结构，
        不用再靠猜。文件在系统临时目录，不进仓库、不影响使用。
        """
        try:
            path = self.ctx_menu_dump_path()
            if os.path.exists(path):
                return                      # 保留第一次的快照
            plain = [(s or "").replace("&", "").strip() for s in labels]
            left = [s for s in plain if s and not any("\u4e00" <= ch <= "\u9fff" for ch in s)]
            lines = [
                "时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                "来源: %s" % kind,
                "条目数: %d" % len(plain),
                "仍是英文的条目: %s" % ("(全部已汉化)" if not left else " | ".join(left)),
                "",
                "条目:",
            ]
            lines += ["  - %s" % s for s in plain]
            if html_text:
                lines += ["", "HTML:", html_text]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError:
            pass

    # ---------- 上次进度（会话） ----------
    def _session_snapshot(self):
        """把当前所有标签整理成可保存的普通字典。"""
        out = []
        for d in self.docs:
            out.append({
                "path": d.path or "",
                "encoding": d.encoding,
                "label": d.label,
                # 有改动、或者磁盘上文件已经没了，都要把正文存进去，
                # 否则下次打开只剩一个空标签（文件被挪走后内容就白写了）
                "text": d.text if (d.modified or not (d.path and os.path.exists(d.path))) else "",
                "modified": bool(d.modified),
                # 锚点是书签，跟着标签走；不写进 .c 文件，所以只能存在会话里
                "anchors": [{"name": a.get("name") or "", "line": int(a.get("line") or 0),
                             "text": a.get("text") or ""} for a in d.anchors],
            })
        return out

    def _save_session(self):
        """把当前进度写进用户目录。失败不影响使用，只是下次从示例开始。"""
        if getattr(self, "_restoring", False):
            return
        if os.environ.get("CTUTOR_NO_SESSION"):
            return                       # 自检模式：不要污染用户真实的"上次进度"
        try:
            self._flush()
            session_mod.save(self._session_snapshot(), self.cur)
        except Exception:                                       # noqa: BLE001
            pass

    def _restore_session(self) -> bool:
        """恢复上次的标签。返回 True 表示确实恢复了东西。"""
        if os.environ.get("CTUTOR_NO_SESSION"):
            return False                 # 自检模式：永远从内置示例开始
        try:
            data = session_mod.load()
        except Exception:                                       # noqa: BLE001
            return False
        items = data.get("docs") or []
        if not items:
            return False
        restored = 0
        missing = 0
        for it in items:
            path = it.get("path") or ""
            stored = it.get("text") or ""
            enc = it.get("encoding") or "mbcs"
            if path and os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        raw = f.read()
                except OSError:
                    # 文件在但读不出来（被占用/没权限）：有存档内容就照样恢复
                    if not stored:
                        missing += 1
                        continue
                    doc = Doc(path=path, encoding=enc, text=stored,
                              label=it.get("label") or "未命名")
                    doc.saved_text = ""
                    doc.modified = True
                else:
                    disk = _norm_text(raw.decode(enc, errors="replace"))
                    text = stored or disk
                    doc = Doc(path=path, encoding=enc, text=text,
                              label=it.get("label") or "未命名")
                    doc.saved_text = disk
                    doc.modified = (text != disk)
            elif path:
                # 文件被删了/移走了。**但只要有存档内容就照样恢复**——
                # 早期版本这里是 continue，结果"文件一挪，那个标签就没了"，
                # 用户看到的就是"上次开的文件少了"。
                if not stored:
                    missing += 1
                    continue
                doc = Doc(path=path, encoding=enc, text=stored,
                          label=it.get("label") or "未命名")
                doc.saved_text = ""
                doc.modified = True
            elif stored:
                # 从没保存过的文档：内容只在会话里
                doc = Doc(path=None, encoding=enc, text=stored,
                          label=it.get("label") or "未命名")
                doc.saved_text = ""
                doc.modified = True
            else:
                continue
            # 锚点：老会话里没有这个字段，默认空；坏数据直接忽略，不影响打开文件
            for a in (it.get("anchors") or []):
                if not isinstance(a, dict):
                    continue
                try:
                    line = int(a.get("line") or 0)
                except (TypeError, ValueError):
                    continue
                if line > 0:
                    doc.anchors.append({"name": str(a.get("name") or ""),
                                        "line": line,
                                        "text": str(a.get("text") or "")[:80]})
            self.docs.append(doc)
            restored += 1
        if not restored:
            self.docs = []
            return False
        try:
            active = int(data.get("active") or 0)
        except (TypeError, ValueError):
            active = 0
        self.cur = max(0, min(active, len(self.docs) - 1))
        self._switching = True
        try:
            self._push_current_to_editor(self.docs[self.cur])
        finally:
            self._switching = False
        self._sync_tabs()
        # 让用户看得见"上次的都在"，而不是自己去数标签
        try:
            msg = "已恢复上次打开的 %d 个文件" % restored
            if missing:
                msg += "（%d 个文件已不在原位置，只有内容，未恢复）" % missing
            self.status.showMessage(msg, 8000)
        except Exception:                                       # noqa: BLE001
            pass
        return True

    # ---------- 对齐（整理缩进） ----------
    def format_code(self):
        """把当前文件的缩进整理规范。

        安全性：engine.format 只改行首缩进和行尾空白，并且自带一道
        "去掉所有空白后内容必须完全一致"的校验。格式化后仍然记下原文，
        按 Ctrl+Shift+Z 可以一步撤回——因为 Monaco 的 set_text 会清空它自己的撤销栈。
        """
        d = self._doc()
        if d is None:
            return
        self._flush()
        before = _norm_text(self.editor.text())
        try:
            after, changed = format_mod.format_c_source(before)
        except Exception as e:                                  # noqa: BLE001
            QMessageBox.warning(self, "对齐失败", "%s: %s" % (type(e).__name__, e))
            return
        if not changed or after == before:
            self.status.showMessage("缩进已经对齐了，没有需要改的地方", 6000)
            return
        d.format_undo = before
        self._switching = True
        try:
            self.editor.set_text(after)
        finally:
            self._switching = False
        self._flush()
        self.status.showMessage(
            "已对齐 %d 行（Ctrl+Shift+Z 可撤回到对齐前）" % changed, 9000)

    def undo_format(self):
        """撤回到上一次整体改写（对齐 / AI 修改）之前的样子。"""
        d = self._doc()
        if d is None or not d.format_undo:
            self.status.showMessage("没有可撤回的修改", 4000)
            return
        self._switching = True
        try:
            self.editor.set_text(d.format_undo)
        finally:
            self._switching = False
        d.format_undo = None
        d.compare_old = ""
        d.compare_new = ""
        self._flush()
        self._sync_compare_button()
        self.status.showMessage("已恢复到修改前的样子", 6000)

    # ---------- AI 修改代码 ----------
    def _ai_fix_running(self) -> bool:
        w = getattr(self, "ai_worker", None)
        return w is not None and w.isRunning()

    def _sync_compare_button(self):
        """「对照」按钮只在"这个文档刚被 AI 改过"时可用。"""
        if not hasattr(self, "btn_compare"):
            return
        d = self._doc()
        self.btn_compare.setEnabled(bool(d and d.compare_old))

    def _code_context(self, diag, source: str, span: int = 3) -> str:
        """取报错行上下几行给模型看，出错行用 --> 标出。"""
        lines = _norm_text(source).split("\n")
        line = diag.line or 0
        if line <= 0 or line > len(lines):
            return ""
        lo = max(1, line - span)
        hi = min(len(lines), line + span)
        out = []
        for i in range(lo, hi + 1):
            mark = "--> " if i == line else "    "
            out.append("%s%4d  %s" % (mark, i, lines[i - 1]))
        return "\n".join(out)

    def _ask_fix_mode(self) -> str:
        """问用户用哪种对照方式。记住过就直接用。"""
        try:
            saved = str(((config_mod.load().get("ui") or {}).get("ai_fix_mode")) or "").strip()
        except Exception:                                       # noqa: BLE001
            saved = ""
        if saved in ("inplace", "window", "inline"):
            return saved
        dlg = AIFixModeDialog(self, "inplace")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return ""
        if dlg.remember():
            try:
                config_mod.save_user_config({"ui": {"ai_fix_mode": dlg.mode}})
            except Exception:                                   # noqa: BLE001
                pass
        return dlg.mode

    def do_ai_fix(self):
        """AI 修改入口：没有诊断就先检查一次，然后问对照方式并开始。"""
        if self._ai_fix_running() or (self.worker is not None and self.worker.isRunning()):
            return
        d = self._doc()
        if d is None:
            return
        if not ai_mod.has_api_key():
            QMessageBox.information(
                self, "需要 DeepSeek 密钥",
                "AI 修改要用 DeepSeek。\n\n"
                "点菜单「AI → 设置 DeepSeek 密钥…」填一个就行。\n"
                "（本地知识库的讲解不联网，不需要密钥。）")
            return
        rep = getattr(self, "_last_report", None)
        chosen = (rep or {}).get("chosen") or {}
        if not rep or not chosen.get("line"):
            # 还没检查过：先检查，检查完在 _on_done 里自动接着走
            self._ai_fix_pending = True
            self.status.showMessage("先检查一下，检查完会自动问你怎么对照…")
            self.do_check()
            return
        self._start_ai_fix(rep)

    def _start_ai_fix(self, rep: dict):
        self._flush()
        chosen = rep.get("chosen") or {}
        src = _norm_text(self.editor.text())
        if not src.strip():
            QMessageBox.information(self, "没有代码", "编辑器里还没有代码。")
            return
        from engine.diagnostic import Diagnostic
        diag = Diagnostic(compiler=rep.get("compiler") or "msvc",
                          message=chosen.get("message", ""),
                          code=chosen.get("code", ""),
                          level=chosen.get("level", "error"),
                          file=chosen.get("file", ""),
                          line=chosen.get("line", 0),
                          column=chosen.get("column", 0))
        mode = self._ask_fix_mode()
        if not mode:
            return
        self._ai_fix_mode = mode
        self.btn_ai_fix.setEnabled(False)
        self.status.showMessage("AI 正在想怎么改…（通常几秒到十几秒）")
        w = AIFixWorker(diag, src, self._code_context(diag, src), self)
        w.done.connect(self._on_ai_fix_done)
        w.failed.connect(self._on_ai_fix_failed)
        self.ai_worker = w
        w.start()

    def _on_ai_fix_failed(self, tb: str):
        self.btn_ai_fix.setEnabled(True)
        self.status.showMessage("AI 修改失败", 6000)
        QMessageBox.critical(self, "AI 修改失败", str(tb)[-1200:])

    def _on_ai_fix_done(self, res):
        self.btn_ai_fix.setEnabled(True)
        self._ai_fix_pending = False
        if not res.ok:
            msg = "没能给出可用的修正代码：\n\n%s" % res.error
            if res.warnings:
                msg += "\n\n过程：" + "；".join(res.warnings[-3:])
            QMessageBox.warning(self, "AI 修改", msg)
            self.status.showMessage("AI 没有给出可用的代码", 6000)
            return
        self._apply_ai_result(res, getattr(self, "_ai_fix_mode", "inplace"))

    def _apply_ai_result(self, res, mode: str):
        """按用户选的对照方式把结果放出去。

        三种方式**都不写文件**：只改编辑器缓冲区，标签会变成"已修改"，
        存不存由用户自己决定。
        """
        d = self._doc()
        if d is None:
            return
        before = _norm_text(self.editor.text())
        chosen = {}
        try:
            chosen = (getattr(self, "_last_report", None) or {}).get("chosen") or {}
        except Exception:                                       # noqa: BLE001
            pass
        changed = len(res.changes)

        if mode == "window":
            win = FixedCodeWindow(self, res.fixed_code, res.changes,
                                  "这是 AI 给的修正版本（%s）。原文件没有任何改动，"
                                  "看完点「应用到编辑器」才生效。" % res.summary())
            win.applied.connect(lambda: self._apply_ai_result(res, "inplace"))
            self._fixed_win = win            # 留住引用，否则窗口会被回收
            win.show()
            win.raise_()
            self.status.showMessage("已打开对照窗口（原文件未改动）", 6000)
            return

        if mode == "inline":
            new_text, n = ai_fix_mod.apply_inline_hints(
                before, res.changes, anchor_line=chosen.get("line", 0))
            if not n:
                QMessageBox.information(
                    self, "无法插入建议",
                    "没找到合适的位置插入建议。\n"
                    "（可能出问题的那一行位于多行字符串或块注释里，插注释会改变程序行为，"
                    "所以这里选择不动它。）")
                return
            d.format_undo = before
            d.compare_old = before
            d.compare_new = new_text
            self._switching = True
            try:
                self.editor.set_text(new_text)
            finally:
                self._switching = False
            self._flush()
            self._sync_compare_button()
            self.status.showMessage("已在 %d 处加了 // AI 建议（原代码未改动）" % n, 9000)
            return

        # 默认：原位修改
        d.format_undo = before
        d.compare_old = before
        d.compare_new = res.fixed_code
        self._switching = True
        try:
            self.editor.set_text(res.fixed_code)
        finally:
            self._switching = False
        self._flush()
        self._sync_compare_button()
        self.status.showMessage(
            "已应用 AI 修改（%d 处）。点「对照」看改了什么；Ctrl+Shift+Z 可以还原" % changed,
            10000)

    def show_ai_compare(self):
        """对照：AI 修改前后的逐行差异。"""
        d = self._doc()
        if d is None or not d.compare_old:
            QMessageBox.information(self, "没有可对照的内容",
                                    "先点「AI 修改」，改完才能对照。")
            return
        DiffDialog(self, d.compare_old, d.compare_new,
                   "对照：AI 改了什么").exec()

    # ---------- 项目 ----------
    def _projects_dir(self) -> str:
        try:
            d = (config_mod.load().get("ui") or {}).get("projects_dir") or ""
        except Exception:                                       # noqa: BLE001
            d = ""
        return d or newproject_mod.default_projects_dir()

    def set_projects_dir(self):
        """设置以后新建文件默认放哪儿。"""
        cur = self._projects_dir()
        p = QFileDialog.getExistingDirectory(self, "选择项目默认存放位置", cur)
        if not p:
            return
        try:
            config_mod.save_user_config({"ui": {"projects_dir": p}})
        except Exception as e:                                  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.status.showMessage("以后新建文件默认放在：%s" % p, 6000)
        QMessageBox.information(self, "已设置",
                                "以后「新建文件」默认放在：\n%s" % p)

    def new_project(self):
        """新建文件：在选定目录里创建一个 .c（扁平存放，不再套文件夹），然后直接打开。"""
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
        self.status.showMessage("已创建：%s" % dlg.result_path, 8000)

    def _open_path(self, path: str):
        """把某个文件作为新标签打开（不弹文件选择框）。已经开过的会直接跳过去。"""
        try:
            enc, _lines = read_source(path)
            with open(path, "rb") as f:
                text = f.read().decode(enc, errors="replace").replace("\r\n", "\n")
        except OSError as e:
            QMessageBox.warning(self, "打开失败", str(e))
            return
        self._open_doc(Doc(path=path, encoding=enc, text=text))

    def _encoding_changed(self):
        """编码下拉框是**当前文档**的属性，切了标签要跟着变。"""
        if self._switching:
            return
        d = self._doc()
        if d is None:
            return
        d.encoding = self.cmb_enc.currentData()
        self._update_file_label()

    # ---------- 检查 ----------
    def _working_copy(self) -> str:
        d = os.path.join(config_mod.path_of("build"), "panel")
        os.makedirs(d, exist_ok=True)
        base = os.path.basename(self.path) if self.path else "untitled.c"
        if not base.lower().endswith((".c", ".h")):
            base += ".c"
        # 前面加文档编号：同时打开两个项目时，两边可能都有 main.c，
        # 不加编号就会互相覆盖，检查结果串到另一个文件上去。
        out = os.path.join(d, "%02d_%s" % (max(self.cur, 0), base))
        enc = self.encoding
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
    def _start_run(self):
        if self.run_worker is not None and self.run_worker.isRunning():
            return
        if self.worker is not None and self.worker.isRunning():
            return
        working = self._working_copy()
        enc = self.encoding
        self.status.showMessage("正在编译…")
        self.btn_run.setEnabled(False)
        self.btn_check.setEnabled(False)
        # 直接切到「运行输出」页：不弹黑框，结果就在这一页里
        self.tabs.setCurrentIndex(1)
        self._run_streamed = False
        self.view_run.setPlainText("正在编译并运行…\n")
        # 输入框里已有的内容作为第一批输入一次性喂进去；然后清空，
        # 运行中这一栏就是"边跑边答"的输入行。
        initial = self.txt_input.toPlainText()
        self.txt_input.clear()
        self.run_worker = RunWorker(working, initial, enc, None,
                                    self.spin_timeout.value(), self)
        self.run_worker.chunk.connect(self._on_run_chunk)
        self.run_worker.done.connect(self._on_run_done)
        self.run_worker.failed.connect(self._on_run_failed)
        self.run_worker.start()
        self.btn_send_input.setEnabled(True)
        self.btn_end_input.setEnabled(True)
        self.btn_stop_run.setEnabled(True)
        # 盯着"突然不出声了"：多半是程序停在 scanf 上等人输入，
        # 不提示的话学生只会看到一片空白，以为程序卡死、运行很慢。
        self._last_chunk_at = time.time()
        self._idle_hint_shown = False
        self._stopped_by_user = False
        self._run_watch.start()

    def do_run(self):
        """F9：编译并运行，输出直接显示在面板的「运行输出」页（不弹命令行窗口）。"""
        self._start_run()

    def _on_run_chunk(self, text: str):
        """程序还在跑时就把输出贴上来——不用等它结束，学生能马上看到结果。"""
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not getattr(self, "_run_streamed", False):
            self._run_streamed = True
            self.view_run.setPlainText("")       # 清掉"正在编译并运行…"
        self._last_chunk_at = time.time()
        cur = self.view_run.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(text)
        self.view_run.setTextCursor(cur)
        self.view_run.ensureCursorVisible()

    def _tick_run_watch(self):
        """运行中每 0.5 秒看一眼：是不是已经憋了 2.5 秒没吐字了。"""
        w = self.run_worker
        if w is None or not w.isRunning():
            self._run_watch.stop()
            return
        if getattr(self, "_idle_hint_shown", True):
            return
        if time.time() - getattr(self, "_last_chunk_at", 0) < 2.5:
            return
        self._idle_hint_shown = True
        self._append_note(
            "（已经 2 秒没有新输出了：如果程序在等 scanf，在下面输入框里输入后回车就行，"
            "等多久都不会超时；如果它不用输入，那就是在算，稍等，或者点右上角「停止」。）")

    def _send_input(self):
        """把输入框里的这一行送进正在运行的程序（半交互）。

        为什么要在输出里回显一遍：程序的标准输入是管道，不是终端——没有 tty
        就不会把学生敲的字回显出来。不回显的话，界面上只看到程序注意一句接一句，
        完全不知道"我刚才到底输了什么"。
        """
        w = self.run_worker
        if w is None or not w.isRunning():
            self.status.showMessage("程序没在运行——先按 F9；运行中才能在这里输入")
            return
        text = self.txt_input.toPlainText()
        if not text.strip():
            # 空着按回车 = 结束输入：学生不用先去点按钮，回车最顺手
            self._end_input()
            return
        w.send(text)
        self._append_echo(text)
        self.txt_input.clear()
        self.txt_input.setFocus()

    def _end_input(self):
        """告诉程序"没有更多输入了"（EOF）。

        为什么需要它：`while (scanf(...) != EOF)`、`gets` 循环这类程序，
        只有读到 EOF 才会往下走；不给 EOF，程序就一直停在那里等下一行输入
        （等输入不算超时，所以会一直等下去，不会自己结束）。
        """
        w = self.run_worker
        if w is None or not w.isRunning():
            self.status.showMessage("程序没在运行——先按 F9；运行中才能结束输入")
            return
        if not w.end_input():
            return
        self._append_note("—— 已结束输入（程序读到 EOF）——")
        # EOF 之后程序不会再读 stdin，两个按钮都收起来，免得学生白点
        self.btn_send_input.setEnabled(False)
        self.btn_end_input.setEnabled(False)

    def _stop_run(self):
        """「停止」：真的卡住时的手动出口。

        等输入的时间已经不算超时了，所以正常情况下用不着这个按钮；
        留着是给"死循环但一直有输出"和"程序睡着不干活"这类情况兜底——
        没有它，半交互模式下唯一能终止程序的办法就是关掉整个面板。
        """
        w = self.run_worker
        if w is None or not w.isRunning():
            self.status.showMessage("程序没在运行——先按 F9")
            return
        if not w.stop():
            return
        self._stopped_by_user = True
        self.btn_stop_run.setEnabled(False)
        self.btn_send_input.setEnabled(False)
        self.btn_end_input.setEnabled(False)
        self._append_note("—— 已请求停止，正在终止程序… ——")
        self.status.showMessage("正在停止…")

    def _append_note(self, text: str):
        cur = self.view_run.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(text + "\n")
        self.view_run.setTextCursor(cur)
        self.view_run.ensureCursorVisible()

    def _append_echo(self, text: str):
        cur = self.view_run.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText("› " + text.replace("\r\n", "\n").rstrip("\n") + "\n")
        self.view_run.setTextCursor(cur)
        self.view_run.ensureCursorVisible()

    def _on_run_failed(self, tb: str):
        self.btn_send_input.setEnabled(False)
        self.btn_end_input.setEnabled(False)
        self.btn_stop_run.setEnabled(False)
        self._run_watch.stop()
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
        self.btn_send_input.setEnabled(False)
        self.btn_end_input.setEnabled(False)
        self.btn_stop_run.setEnabled(False)
        self._run_watch.stop()

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
        # 用 <pre> 原样呈现：程序输出里的空格、缩进、制表符都是内容的一部分。
        # 已经边跑边贴过输出时只补一行结尾小结，不整段重画——否则屏幕会闪一下，
        # 而且学生刚看到的内容会被"重排"。
        self.tabs.setCurrentIndex(1)
        if getattr(self, "_run_streamed", False):
            self._append_run_footer(res)
        else:
            self.view_run.setHtml(
                '<pre style="font-family:Consolas,\'Microsoft YaHei\',monospace;'
                'font-size:11pt;white-space:pre-wrap;margin:10px;">%s</pre>'
                % html.escape(text))

        # 状态栏要给"具体是哪一种失败"，不能笼统说一句"没成功" ——
        # 「多半是死循环」这类提示是初学者最需要的那句话。
        if res.timed_out:
            self.status.showMessage(
                "程序连续算了太久，已终止（等输入的时间不算，可能是死循环）")
        elif res.exit_code == -15:
            self.status.showMessage("运行已手动停止")
        elif res.ok:
            self.status.showMessage("运行结束　用时 %.2f 秒" % res.duration)
        elif res.exit_code:
            self.status.showMessage("程序异常退出（返回码 %d），原因见下方" % res.exit_code)
        else:
            self.status.showMessage("运行没有成功，详情见下方")

    def _append_run_footer(self, res):
        """输出是边跑边贴的，结束后只在末尾补一小段小结（不再整段重画）。"""
        lines = [""]
        for n in (res.notes or []):
            lines.append("· " + n)
        if res.timed_out:
            lines.append("—— 连续算了太久被终止（等输入的时间不算）——")
        elif res.exit_code == -15:
            lines.append("—— 已手动停止 ——")
        elif res.ok:
            lines.append("—— 运行结束，用时 %.2f 秒 ——" % res.duration)
        else:
            lines.append("—— 程序异常结束，返回码 %d ——" % res.exit_code)
        cur = self.view_run.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText("\n".join(lines) + "\n")
        self.view_run.setTextCursor(cur)
        self.view_run.ensureCursorVisible()

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
        # 用户点了「AI 修改」但还没检查过：检查完自动接着走，不用点第二次
        if getattr(self, "_ai_fix_pending", False):
            self._ai_fix_pending = False
            if ch.get("line"):
                QTimer.singleShot(200, lambda: self._start_ai_fix(rep))
            else:
                QMessageBox.information(
                    self, "AI 修改",
                    "这次编译没有可定位的错误行，AI 不知道要改哪里。")

    def _rerender_current(self):
        """只改了"讲到第几层"这类参数时重画：内容变了，但阅读位置该保留。"""
        if getattr(self, "_last_report", None):
            self._render_report(self._last_report, keep_scroll=True)

    def _render_report(self, rep: dict, keep_scroll: bool = False):
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
            self._show_explanation(keep_scroll)
            return

        exp = rep.get("explanation")
        if not exp:
            self._explanation_html = "".join(head) + "<p>没有可展示的诊断。</p>"
            self._show_explanation(keep_scroll)
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
                          '提示：一次报很多条通常<b>不是很多个错误</b>——'
                          '第一条往往是根因，修好它之后后面那些多半会一起消失。'
                          '先只改下面讲的那一处，再重新检查。</div>')
        self._explanation_html = "".join(head) + extra + render_mod.render_html(e, depth)
        self._show_explanation(keep_scroll)

    def _show_explanation(self, keep_scroll: bool = False):
        """展示讲解。使用说明已是独立窗口，不会再被讲解顶掉。"""
        self.tabs.setCurrentIndex(0)             # 切回「讲解」页
        self._apply_view(keep_scroll)

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
            # 输入框里回车 = 把这一行送进正在运行的程序（Shift+回车还是换行）
            if (ev.type() == QEvent.KeyPress
                    and obj is getattr(self, "txt_input", None)
                    and ev.key() in (Qt.Key_Return, Qt.Key_Enter)
                    and not (ev.modifiers() & Qt.ShiftModifier)):
                self._send_input()
                return True
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
    # 自检必须**可重复**：不能读用户真实的"上次进度"，否则打包验证会随开发机的
    # 使用状态而时好时坏（真踩过：自检读到了用户会话里的 4 个文件，
    # 编译不报错 → 验证失败；更糟的是可能把示例写回用户会话里）。
    if args.selftest or args.offscreen:
        os.environ["CTUTOR_NO_SESSION"] = "1"

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
