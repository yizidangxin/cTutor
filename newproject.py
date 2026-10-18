# -*- coding: utf-8 -*-
"""新建文件对话框。

需求演进过一轮：
  一开始是"新建项目 → 一个文件夹 + 里面的 main.c"；
  后来改成**扁平存放**——文件直接放在你选的目录里，不再套一层同名文件夹。

为什么改：学生打开资源管理器只看到
    ctutor项目 / 第一次作业 / main.c
要进两层才找到自己那份代码，交作业还得再点进去。而"项目"这个概念
对刚学 C 的人来说也没有任何额外用处——**他们需要的就是一个 .c 文件**。

所以现在：
    ctutor项目 / 第一次作业.c
一眼看到、直接发微信、直接拷给老师。
"""
import os

from PySide6.QtCore import Qt                                        # noqa: F401
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

TEMPLATES = [
    ("空白文件", "",
     ""),
    ("Hello World", "hello",
     '#include <stdio.h>\n'
     '\n'
     'int main(void) {\n'
     '    printf("Hello, World!\\n");\n'
     '    return 0;\n'
     '}\n'),
    ("读入两个数并求和（含 scanf）", "sum",
     '#include <stdio.h>\n'
     '\n'
     'int main(void) {\n'
     '    int a, b;\n'
     '    printf("请输入两个整数，用空格隔开：");\n'
     '    if (scanf("%d %d", &a, &b) != 2) {\n'
     '        printf("输入格式不对\\n");\n'
     '        return 1;\n'
     '    }\n'
     '    printf("%d + %d = %d\\n", a, b, a + b);\n'
     '    return 0;\n'
     '}\n'),
    ("带循环和数组", "loop",
     '#include <stdio.h>\n'
     '\n'
     'int main(void) {\n'
     '    int n = 5;\n'
     '    int a[5] = {3, 1, 4, 1, 5};\n'
     '    int sum = 0;\n'
     '\n'
     '    for (int i = 0; i < n; i++) {\n'
     '        printf("a[%d] = %d\\n", i, a[i]);\n'
     '        sum += a[i];\n'
     '    }\n'
     '    printf("总和 = %d\\n", sum);\n'
     '    return 0;\n'
     '}\n'),
]

# 明显的非法字符：Windows 文件名里不能出现这些
_BAD_CHARS = '\\/:*?"<>|'


def default_projects_dir() -> str:
    """默认的存放位置：文档\\ctutor项目（文件直接放这里面，不再分子文件夹）。"""
    home = os.path.expanduser("~")
    for name in ("Documents", "文档"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return os.path.join(p, "ctutor项目")
    return os.path.join(home, "ctutor项目")


def strip_c_ext(name: str) -> str:
    """把用户输入的 .c/.h 后缀去掉，统一由程序补。避免出现 作业1.c.c。"""
    n = (name or "").strip()
    low = n.lower()
    for ext in (".c", ".h"):
        if low.endswith(ext):
            return n[: -len(ext)]
    return n


def validate_name(name: str):
    """返回 (是否合法, 原因)。"""
    name = strip_c_ext(name)
    if not name.strip():
        return False, "文件名不能为空"
    if name in (".", ".."):
        return False, "这个名字不能用"
    for ch in _BAD_CHARS:
        if ch in name:
            return False, "文件名里不能有这些字符：%s" % _BAD_CHARS
    if name.endswith(".") or name.endswith(" "):
        return False, "文件名不能以点或空格结尾（Windows 会存不住）"
    return True, ""


class NewProjectDialog(QDialog):
    """新建文件。返回的 result_path 是最终要打开的 .c 文件绝对路径。

    类名保留 NewProjectDialog 是为了不破坏已有的导入与测试；
    实际语义已经变成"新建文件"。
    """

    def __init__(self, parent=None, default_dir: str = "", default_encoding: str = "mbcs"):
        super().__init__(parent)
        self.setWindowTitle("新建文件")
        self.result_path = ""
        self.projects_dir = default_dir or default_projects_dir()
        self.encoding = default_encoding

        form = QFormLayout()
        self.ed_name = QLineEdit("作业1")
        self.ed_name.setPlaceholderText("英文或中文都行，例如：第一次作业")
        self.ed_name.selectAll()
        form.addRow("文件名：", self.ed_name)

        row = QHBoxLayout()
        self.ed_dir = QLineEdit(self.projects_dir)
        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.ed_dir, 1)
        row.addWidget(self.btn_browse)
        form.addRow("存放位置：", row)

        self.cmb_tpl = QComboBox()
        for label, _slug, _body in TEMPLATES:
            self.cmb_tpl.addItem(label)
        self.cmb_tpl.setCurrentIndex(1)          # 默认 Hello World
        form.addRow("起始内容：", self.cmb_tpl)

        self.chk_open = QCheckBox("创建后立即打开")
        self.chk_open.setChecked(True)
        form.addRow("", self.chk_open)

        self.lbl_preview = QLabel()
        self.lbl_preview.setStyleSheet("color:#4B5563;")
        self.lbl_preview.setWordWrap(True)
        form.addRow("", self.lbl_preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("创建")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

        self.ed_name.textChanged.connect(self._refresh_preview)
        self.ed_dir.textChanged.connect(self._refresh_preview)
        self._refresh_preview()
        self.resize(560, 300)

    # ---------------------------------------------------------------- 交互
    def _browse(self):
        p = QFileDialog.getExistingDirectory(self, "选择存放位置", self.ed_dir.text())
        if p:
            self.ed_dir.setText(p)

    def planned_path(self) -> str:
        name = strip_c_ext(self.ed_name.text().strip()) or "…"
        return os.path.join(self.ed_dir.text().strip() or "…", name + ".c")

    def _refresh_preview(self):
        ok, why = validate_name(self.ed_name.text())
        if ok:
            self.lbl_preview.setText("将创建：%s" % self.planned_path())
            self.lbl_preview.setStyleSheet("color:#4B5563;")
        else:
            self.lbl_preview.setText("文件名有问题：%s" % why)
            self.lbl_preview.setStyleSheet("color:#B91C1C;")

    def _accept(self):
        ok, why = validate_name(self.ed_name.text())
        if not ok:
            QMessageBox.warning(self, "文件名不对", why)
            return
        base = self.ed_dir.text().strip()
        if not base:
            QMessageBox.warning(self, "缺少位置", "请选择存放位置。")
            return
        name = strip_c_ext(self.ed_name.text().strip())
        target = os.path.join(base, name + ".c")

        if os.path.exists(target):
            # 不覆盖别人的东西：问清楚再说
            r = QMessageBox.question(
                self, "文件已存在",
                "%s 已经存在。\n\n要覆盖它吗？" % target,
                QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        try:
            os.makedirs(base, exist_ok=True)
            _idx, _slug, body = TEMPLATES[self.cmb_tpl.currentIndex()]
            with open(target, "w", encoding=self.encoding, errors="replace",
                      newline="\n") as f:
                f.write(body)
        except OSError as e:
            QMessageBox.critical(self, "创建失败", "没能创建文件：\n%s" % e)
            return

        self.projects_dir = base
        self.result_path = target
        self.accept()

    # ---------------------------------------------------------------- 供测试
    def create_only(self):
        """给测试用：不弹窗，直接按当前输入创建。"""
        self._accept()
        return self.result_path


# 语义别名
NewFileDialog = NewProjectDialog
