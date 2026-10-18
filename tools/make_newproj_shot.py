# -*- coding: utf-8 -*-
"""生成 README 用的「新建文件」对话框截图。

离屏渲染要点（和 make_tabs_shot.py 同一个坑）：
**必须 show()**，否则控件不布局、抓出来是空白；抓之前也要给足事件循环时间。
对话框内容里有中文，所以之前要先装好中文字体。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtWidgets import QApplication          # noqa: E402
import panel                                        # noqa: E402
import newproject as np_mod                         # noqa: E402


def pump(app, n=25, delay=0.08):
    for _ in range(n):
        app.processEvents()
        time.sleep(delay)


def main(out_path):
    app = QApplication([])
    panel.ensure_cjk_font(app)

    dlg = np_mod.NewProjectDialog(default_dir=os.path.join(ROOT, "build", "新建示例"))
    dlg.ed_name.setText("第一次作业")
    dlg.cmb_tpl.setCurrentIndex(1)
    dlg.show()
    pump(app, 30)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    ok = dlg.grab().save(out_path)
    print("预览文字：%s" % dlg.lbl_preview.text())
    print("截图：%s（成功=%s）" % (out_path, ok))
    return 0 if ok else 1


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "docs", "screenshots", "panel_newproj.png")
    raise SystemExit(main(target))
