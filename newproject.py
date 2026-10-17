# -*- coding: utf-8 -*-
"""新建项目对话框。

需求是"新建项目，然后设置存储路径"，所以一个对话框里同时给出：
  · 项目名
  · 存放位置（可浏览、可记住上次用的位置）
  · 起始模板

设计取舍：不做"解决方案/工程文件"那一套。这是给初学者看报错用的工具，
一个项目 = 一个文件夹 + 一个 main.c，学生能直接在资源管理器里找到、能拷给老师。
多余的概念只会增加负担。
"""
import os

from PySide6.QtCore import Qt
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
    """默认的存放位置：文档\\ctutor项目。"""
    home = os.path.expanduser("~")
    for name in ("Documents", "文档"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return os.path.join(p, "ctutor项目")
    return os.path.join(home, "ctutor项目")


def validate_name(name: str):
    """返回 (是否合法, 原因)。"""
    name = (name or "").strip()
    if not name:
        return False, "项目名不能为空"
    if name in (".", ".."):
        return False, "这个名字不能用"
    for ch in _BAD_CHARS:
        if ch in name:
            return False, "项目名里不能有这些字符：%s" % _BAD_CHARS
    if name.endswith(".") or name.endswith(" "):
        return False, "项目名不能以点或空格结尾（Windows 会存不住）"
    return True, ""


class NewProjectDialog(QDialog):
    """新建项目。返回的 result_path 是最终要打开的 .c 文件绝对路径。"""

    def __init__(self, parent=None, default_dir: str = "", default_encoding: str = "mbcs"):
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.result_path = ""
        self.projects_dir = default_dir or default_projects_dir()
        self.encoding = default_encoding

        form = QFormLayout()
        self.ed_name = QLineEdit("myproject")
        self.ed_name.setPlaceholderText("英文或中文都行，例如：第一次作业")
        self.ed_name.selectAll()
        form.addRow("项目名：", self.ed_name)

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
        form.addRow("起始模板：", self.cmb_tpl)

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

    def _refresh_preview(self):
        name = self.ed_name.text().strip() or "…"
        path = os.path.join(self.ed_dir.text().strip() or "…", name)
        ok, why = validate_name(self.ed_name.text())
        if ok:
            self.lbl_preview.setText("将创建：%s\\main.c" % path)
            self.lbl_preview.setStyleSheet("color:#4B5563;")
        else:
            self.lbl_preview.setText("项目名有问题：%s" % why)
            self.lbl_preview.setStyleSheet("color:#B91C1C;")

    def _accept(self):
        ok, why = validate_name(self.ed_name.text())
        if not ok:
            QMessageBox.warning(self, "项目名不对", why)
            return
        name = self.ed_name.text().strip()
        base = self.ed_dir.text().strip()
        if not base:
            QMessageBox.warning(self, "缺少位置", "请选择存放位置。")
            return
        folder = os.path.join(base, name)
        main_c = os.path.join(folder, "main.c")

        if os.path.exists(main_c):
            # 不覆盖别人的东西：问清楚再说
            r = QMessageBox.question(
                self, "文件已存在",
                "%s 已经存在。\n\n要覆盖它吗？" % main_c,
                QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        try:
            os.makedirs(folder, exist_ok=True)
            _idx, _slug, body = TEMPLATES[self.cmb_tpl.currentIndex()]
            with open(main_c, "w", encoding=self.encoding, errors="replace",
                      newline="\n") as f:
                f.write(body)
        except OSError as e:
            QMessageBox.critical(self, "创建失败", "没能创建项目：\n%s" % e)
            return

        self.projects_dir = base
        self.result_path = main_c
        self.accept()

    # ---------------------------------------------------------------- 供测试
    def create_only(self):
        """给测试用：不弹窗，直接按当前输入创建。"""
        self._accept()
        return self.result_path
