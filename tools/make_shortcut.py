#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成桌面快捷方式与图标，让面板可以双击直接启动（不弹黑框）。

为什么要有这个脚本而不是手工建快捷方式：
  - DSH 自带的 Python 路径很长，手工填容易错；
  - 项目一旦移动位置，重跑一次即可修好；
  - 用 pythonw.exe 启动才不会有黑色控制台窗口。

用法：
    python tools/make_shortcut.py                 # 建到桌面
    python tools/make_shortcut.py --name 我的名字   # 自定义快捷方式名称
    python tools/make_shortcut.py --dir <目录>     # 建到指定目录
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

ICON = os.path.join(ROOT, "assets", "ctutor.ico")
DEFAULT_NAME = "C语言报错助手"


def find_pythonw() -> str:
    """优先用同目录下的 pythonw.exe（无控制台），找不到就退回当前解释器。"""
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(cand):
        return cand
    return sys.executable


def make_icon() -> bool:
    """画一个简单图标：深蓝圆角方块 + 白色 C + 橙色圆点（对应讲解里的 ★ 提示）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("未安装 Pillow，跳过图标生成（快捷方式仍会创建）")
        return False
    os.makedirs(os.path.dirname(ICON), exist_ok=True)
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, S - 6, S - 6], radius=52, fill=(31, 59, 87, 255))
    font = None
    for f in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\segoeui.ttf",
              r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(f):
            try:
                font = ImageFont.truetype(f, 158)
                break
            except OSError:
                continue
    if font:
        d.text((S // 2 - 6, S // 2 - 6), "C", font=font, fill=(255, 255, 255, 255),
               anchor="mm")
    else:
        d.ellipse([70, 70, 186, 186], outline=(255, 255, 255, 255), width=22)
    d.ellipse([S - 84, S - 84, S - 34, S - 34], fill=(217, 119, 6, 255))
    img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                          (128, 128), (256, 256)])
    print("图标已生成：%s" % ICON)
    return True


def make_bat() -> str:
    """再放一个 .bat 兜底：快捷方式万一坏了还能用。"""
    bat = os.path.join(ROOT, "启动面板.bat")
    py = find_pythonw()
    with open(bat, "w", encoding="mbcs") as f:
        f.write("@echo off\r\n")
        f.write('cd /d "%~dp0"\r\n')                     # %~dp0 不是格式化占位符，原样写入
        f.write('start "" "%s" "%%~dp0panel.py"\r\n' % py)
    print("兜底启动脚本：%s" % bat)
    return bat


def make_shortcut(target_dir: str, name: str) -> bool:
    pythonw = find_pythonw()
    panel = os.path.join(ROOT, "panel.py")
    lnk = os.path.join(target_dir, name + ".lnk")
    ps = (
        '$ws = New-Object -ComObject WScript.Shell; '
        '$s = $ws.CreateShortcut("%s"); '
        '$s.TargetPath = "%s"; '
        "$s.Arguments = '\"%s\"'; "
        '$s.WorkingDirectory = "%s"; '
        '%s'
        '$s.Description = "ctutor — 帮你读懂 C 语言编译报错"; '
        '$s.WindowStyle = 1; '
        '$s.Save(); '
        'Write-Output "OK"'
    ) % (lnk, pythonw, panel, ROOT,
         ('$s.IconLocation = "%s"; ' % ICON) if os.path.exists(ICON) else "")
    # PowerShell 里单引号字符串中的 \" 原样保留，正是 Arguments 需要的引号
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                          capture_output=True)
    out = (proc.stdout or b"").decode("mbcs", errors="replace")
    err = (proc.stderr or b"").decode("mbcs", errors="replace")
    if proc.returncode == 0 and "OK" in out and os.path.exists(lnk):
        print("桌面快捷方式：%s" % lnk)
        return True
    print("创建快捷方式失败：%s %s" % (out.strip(), err.strip()[:300]))
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成桌面快捷方式，双击即可启动面板")
    ap.add_argument("--name", default=DEFAULT_NAME, help="快捷方式名称")
    ap.add_argument("--dir", default=os.path.join(os.path.expanduser("~"), "Desktop"),
                    help="放快捷方式的目录，默认桌面")
    ap.add_argument("--no-icon", action="store_true", help="跳过图标生成")
    args = ap.parse_args(argv)

    print("项目目录：%s" % ROOT)
    print("启动器  ：%s" % find_pythonw())
    print()
    if not args.no_icon:
        make_icon()
    make_bat()
    ok = make_shortcut(args.dir, args.name)
    print()
    if ok:
        print("完成 —— 回到桌面双击「%s」即可启动，不会弹黑框。" % args.name)
        print("（图标可自行改名：重新运行本脚本并加 --name 你的名字）")
        return 0
    print("快捷方式没建成，可以改用 .bat，或直接运行：python panel.py")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
