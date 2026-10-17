# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

几个关键点（都是实测踩出来的）：

1. **qtmonaco 的 Monaco 静态资源**（js_build/ 里的 20MB 编辑器本体）必须一起打包，
   否则界面出来是个空白框。
2. **QtWebEngine 的运行时**（QtWebEngineProcess.exe、locales、resources）同理，
   缺了 Monaco 根本加载不出来。
3. **排除 Python LSP 那一整套依赖**：qtmonaco 会连带装上 python-lsp-server、jedi、
   rope、black、flake8、yapf…… 我们只用它显示 C 代码，一个都用不上，
   而且它们的 `sys.executable -m pylsp` 在打包后会变成"启动自己"。
   代码里已经把它拦掉（panel.py 覆盖 get_pylsp_host），这里再把体积也省掉。
4. 入口用 main.py：不带参数开图形面板，带子命令走命令行。
"""
import os

from PyInstaller.utils.hooks import collect_all

PROJECT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

datas = []
binaries = []
hiddenimports = []

# 1) Monaco 编辑器的静态资源
for pkg in ("qtmonaco",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 2) QtWebEngine 运行时资源
for pkg in ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
            "PySide6.QtWebChannel"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:                                  # noqa: BLE001
        print("collect_all(%s) 跳过：%s" % (pkg, exc))

# 3) 自己的包（分析器通常能自动发现，显式列出更稳）
hiddenimports += ["engine", "engine.report", "engine.compilers", "engine.completion",
                  "engine.editorui", "engine.run", "newproject"]

EXCLUDES = [
    # Python LSP 全家桶 —— 用不上，且在打包后会造成"启动自己"
    "jedi", "rope", "pylsp", "python_lsp_server", "pylsp_jsonrpc",
    "black", "flake8", "yapf", "autopep8", "pydocstyle", "pyflakes",
    "pycodestyle", "mccabe", "snowballstemmer", "whatthepatch", "ujson",
    # 与本项目无关的大件
    "tkinter", "matplotlib", "numpy", "pandas", "scipy", "PIL",
    "pytest", "setuptools", "pip", "wheel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtPdf", "PySide6.QtSvg", "PySide6.QtNetworkAuth",
]

a = Analysis(
    [os.path.join(PROJECT, "main.py")],
    pathex=[PROJECT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ctutor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # 双击不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PROJECT, "assets", "ctutor.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ctutor",
    # 注：曾试过 contents_directory="_r" 来给长路径腾 8 个字符，但 PyInstaller
    # 6.22 忽略了该参数（仍生成 _internal）。而且包内最深路径由编译器工具链决定
    # （bin\gcc\lib\gcc\<target>\<版本>\include\... = +79），改这里不影响最大值，
    # 所以不再折腾。真正的手段是：缩短工具链目录名 + 文档要求解压到短路径。
)
