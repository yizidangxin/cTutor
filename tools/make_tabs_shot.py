# -*- coding: utf-8 -*-
"""生成 README 用的多标签截图。

踩过的坑（都写在这里，免得以后重拍又掉坑里）：
1. **必须 win.show()**，否则控件没布局，webview 不绘制，抓出来编辑器是白块。
2. 离屏模式下 Monaco **改文本后只重绘局部**，中文行会留下叠影。
   解法：最后改一次窗口尺寸再改回来，强制整块重排。
3. 想让标签出现 * 号，**不要真的编辑文本**（那就会触发上面第 2 条的叠影）。
   直接改模型的 saved_text 即可，纯状态变化，不需要重绘。
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

FILES = {
    "第01题_求两数之和.c":
        '#include <stdio.h>\n\nint main(void) {\n    int a, b;\n'
        '    scanf("%d %d", &a, &b);\n    printf("和是 %d\\n", a + b);\n'
        '    return 0;\n}\n',
    "第02题_判断素数.c":
        '#include <stdio.h>\n\nint main(void) {\n    int n, i;\n'
        '    scanf("%d", &n);\n    for (i = 2; i < n; i++)\n'
        '        if (n % i == 0) break;\n'
        '    printf(i == n ? "是素数\\n" : "不是素数\\n");\n'
        '    return 0;\n}\n',
    "第03题_数组排序.c":
        '#include <stdio.h>\n\nint main(void) {\n    int a[5] = {5, 3, 1, 4, 2};\n'
        '    int i, j, t;\n    for (i = 0; i < 5; i++)\n'
        '        for (j = i + 1; j < 5; j++)\n'
        '            if (a[i] > a[j]) { t = a[i]; a[i] = a[j]; a[j] = t; }\n'
        '    for (i = 0; i < 5; i++) printf("%d ", a[i]);\n'
        '    printf("\\n");\n    return 0;\n}\n',
}


def pump(app, n=20, delay=0.08):
    for _ in range(n):
        app.processEvents()
        time.sleep(delay)


def main(out_path: str) -> int:
    app = QApplication([])
    win = panel.MainWindow()
    win.resize(1360, 880)
    win.show()                                   # ← 关键
    pump(app, 50, 0.1)

    d = os.path.join(ROOT, "build", "shot_docs")
    os.makedirs(d, exist_ok=True)
    paths = []
    for name, src in FILES.items():
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        paths.append(p)

    for p in paths:
        win._open_path(p)
    pump(app, 20)

    win.tabbar.setCurrentIndex(1)
    pump(app, 25)

    # 制造"已修改"状态，但不碰编辑器（避免重绘叠影）
    doc = win._doc()
    doc.saved_text = ""
    doc.modified = True
    win._sync_tabs()
    pump(app, 10)

    # 强制整块重排，冲掉 Monaco 的局部重绘残留
    win.resize(1359, 879)
    pump(app, 25)
    win.resize(1360, 880)
    pump(app, 45)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    win.grab().save(os.path.join(ROOT, "build", "_discard.png"))
    pump(app, 20)
    ok = win.grab().save(out_path)
    print("标签：%s" % [win.tabbar.tabText(i) for i in range(win.tabbar.count())])
    print("截图：%s（成功=%s）" % (out_path, ok))
    return 0 if ok else 1


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "docs", "screenshots", "panel_tabs.png")
    raise SystemExit(main(target))
