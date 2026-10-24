# -*- coding: utf-8 -*-
"""导出当前面板的按钮与菜单结构（用于写文档，不是测试）。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
os.environ.setdefault("CTUTOR_NO_SESSION", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tempfile                                            # noqa: E402
from engine import config as config_mod                     # noqa: E402
_TMP = tempfile.mkdtemp(prefix="dump_")
config_mod.user_config_path = lambda: os.path.join(_TMP, "config.json")

from PySide6.QtWidgets import QApplication, QPushButton     # noqa: E402
import panel                                               # noqa: E402


def main():
    app = QApplication([])
    win = panel.MainWindow()
    win.show()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.05)

    print("=" * 60)
    print("工具栏按钮（从左到右）")
    print("=" * 60)
    for b in win.findChildren(QPushButton):
        if not b.isVisible():
            continue
        y = b.mapTo(win, b.rect().topLeft()).y()
        if y > 120:                       # 只要顶部工具栏那一行
            continue
        print("  [%s]  可用=%s  提示=%r" % (
            b.text(), b.isEnabled(), (b.toolTip() or "").split("\n")[0][:60]))

    print()
    print("=" * 60)
    print("菜单")
    print("=" * 60)
    for top in win.menuBar().actions():
        m = top.menu()
        if not m:
            continue
        print("[%s]" % top.text())
        for a in m.actions():
            if a.isSeparator():
                print("   ---")
                continue
            sc = a.shortcut().toString()
            chk = "（可勾选）" if a.isCheckable() else ""
            print("   %s%-26s %s%s" % (
                "● " if (a.isCheckable() and a.isChecked()) else "  ",
                a.text(), sc, chk))

    win._dirty_timer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
