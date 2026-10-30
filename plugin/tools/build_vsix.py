# -*- coding: utf-8 -*-
"""把插件打成 .vsix（不依赖 node / vsce，直接用 zip 结构手写）。

为什么手写：这台机器上没有 node，而 .vsix 本质上就是一个特定目录结构的 zip。
VS Code 只认这几样东西：

    extension/package.json          ← 清单（必须有）
    extension/extension.js          ← 入口
    extension.vsixmanifest          ← 包描述
    [Content_Types].xml             ← OPC 内容类型
    _rels/.rels                     ← 指向 vsixmanifest 的关系

顺带做一件重要的事：把内核（engine/ + data/kb/）复制进 sidecar/，
这样装到 %USERPROFILE%\\.vscode\\extensions 下也能找到 engine 与知识库，
不依赖什么"当前目录"。

用法：
    python tools/build_vsix.py            # 产出 dist/ctutor-<版本>.vsix
    python tools/build_vsix.py --no-engine # 只打壳（体积小，调试用）
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
REPO = os.path.dirname(PLUGIN)

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json"/>
  <Default Extension=".js" ContentType="application/javascript"/>
  <Default Extension=".py" ContentType="application/octet-stream"/>
  <Default Extension=".html" ContentType="text/html"/>
  <Default Extension=".png" ContentType="image/png"/>
  <Default Extension=".md" ContentType="text/markdown"/>
  <Default Extension=".txt" ContentType="text/plain"/>
  <Default Extension=".vsixmanifest" ContentType="text/xml"/>
  <Default Extension=".xml" ContentType="text/xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="utf-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Type="http://schemas.microsoft.com/developer/vsx-schema/2011/Relationships/Manifest"
                Target="/extension.vsixmanifest" Id="R1" />
</Relationships>
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0"
    xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"
    xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="zh-CN" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Tags>{keywords}</Tags>
    <Categories>{categories}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Icon>extension/media/icon.png</Icon>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
      <Property Id="Microsoft.VisualStudio.Services.Links.Source" Value="{repo}" />
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest"
           Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Icons.Default"
           Path="extension/media/icon.png" Addressable="true" />
  </Assets>
</PackageManifest>
"""


def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# 抽 Python 运行时时要保留什么、丢掉什么。
# 判断依据：engine/ 只 import 标准库（codecs ctypes dataclasses difflib glob hashlib html
# itertools json os re shutil subprocess sys threading time traceback typing urllib），
# 所以 site-packages / tcl / include / libs / 各种测试包全都可以不要。
PY_KEEP_FILES = ("python.exe", "python3.dll", "python312.dll", "python311.dll", "python310.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt")
PY_DROP_LIB = {"site-packages", "test", "tests", "idlelib", "ensurepip", "venv", "lib2to3",
               "turtledemo", "tkinter", "pydoc_data", "distutils", "__pycache__", "lib-dynload",
               "sqlite3", "xmlrpc", "wsgiref", "curses", "dbm", "zoneinfo", "turtle.py"}
# 注意：email / http / logging / asyncio 不能丢 —— urllib.request 会 import email。


def bundle_python(src, dst):
    """从本机 Python 安装目录抽一份能跑 sidecar 的最小运行时。

    为什么这么做：学生机上最常见的失败不是"插件坏了"，而是"根本没装 Python"。
    带一份 30 MB 的标准库进去，比写十行安装说明有用。
    """
    if not os.path.isfile(os.path.join(src, "python.exe")):
        return 0, 0.0
    os.makedirs(dst, exist_ok=True)
    n = 0
    for name in PY_KEEP_FILES:
        p = os.path.join(src, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, name))
            n += 1
    # .pyd 扩展模块（_socket / _ssl / unicodedata / select …）都在 DLLs 里，必须带
    dlls = os.path.join(src, "DLLs")
    if os.path.isdir(dlls):
        for name in os.listdir(dlls):
            p = os.path.join(dlls, name)
            if os.path.isfile(p) and name.endswith((".pyd", ".dll")):
                os.makedirs(os.path.join(dst, "DLLs"), exist_ok=True)
                shutil.copy2(p, os.path.join(dst, "DLLs", name))
                n += 1
    lib = os.path.join(src, "Lib")
    if os.path.isdir(lib):
        for name in os.listdir(lib):
            if name in PY_DROP_LIB:
                continue
            p = os.path.join(lib, name)
            if os.path.isfile(p):
                if name.endswith((".pyc", ".pyo")):
                    continue
                os.makedirs(os.path.join(dst, "Lib"), exist_ok=True)
                shutil.copy2(p, os.path.join(dst, "Lib", name))
                n += 1
            elif os.path.isdir(p):
                n += copy_tree(p, os.path.join(dst, "Lib", name),
                               lambda f, b: not f.endswith((".pyc", ".pyo")))
    total = 0
    for base, _dirs, files in os.walk(dst):
        for f in files:
            total += os.path.getsize(os.path.join(base, f))
    return n, total / (1024.0 * 1024.0)


def selfcheck(ext):
    """用包里那份运行时把内核 import 一遍。

    裁剪标准库最容易犯的错是"删多了"——engine 只用标准库，但 urllib.request 会连带
    import email / http。所以不靠眼看，装包前先真跑一次。
    """
    py = os.path.join(ext, "runtime", "python.exe")
    if not os.path.isfile(py):
        return True, "（包里没有内置运行时，跳过自检）"
    sidecar = os.path.join(ext, "sidecar")
    code = ("import sys;sys.path.insert(0, %r);"
            "import engine.report, engine.explain, engine.fixit, engine.autofix, engine.run,"
            "engine.render, engine.kb, engine.config, engine.ai, engine.ai_explain;"
            "print('SELFCHECK-OK')") % sidecar
    try:
        p = subprocess.run([py, "-X", "utf8", "-c", code], capture_output=True,
                           text=True, timeout=180)
    except Exception as e:                                       # noqa: BLE001
        return False, "自检启动失败：%s" % e
    if "SELFCHECK-OK" in (p.stdout or ""):
        return True, "包内运行时能把内核 import 起来"
    return False, ((p.stdout or "") + (p.stderr or "")).strip()[-800:]


def copy_tree(src, dst, pattern_filter=None):
    """复制目录，跳过 __pycache__ 与 .pyc。"""
    n = 0
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".cache", "build", "log")]
        rel = os.path.relpath(base, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                continue
            if pattern_filter and not pattern_filter(f, base):
                continue
            shutil.copy2(os.path.join(base, f), os.path.join(target, f))
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-engine", action="store_true", help="不把内核复制进包（仅调试壳）")
    ap.add_argument("--no-python", action="store_true", help="不带 Python 运行时（要求用户自己装了 Python）")
    ap.add_argument("--python-src", default="", help="从哪个 Python 安装目录抽运行时")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with io.open(os.path.join(PLUGIN, "package.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    version = pkg["version"]

    if not args.no_engine and not os.path.isdir(os.path.join(REPO, "engine")):
        print("找不到内核目录：%s" % os.path.join(REPO, "engine"), file=sys.stderr)
        return 2

    stage = os.path.join(PLUGIN, ".stage")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    ext = os.path.join(stage, "extension")
    os.makedirs(ext)

    for name in ("extension.js", "package.json", "README.md", "LICENSE", ".vscodeignore"):
        src = os.path.join(PLUGIN, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(ext, name))
    copy_tree(os.path.join(PLUGIN, "media"), os.path.join(ext, "media"))
    copy_tree(os.path.join(PLUGIN, "sidecar"), os.path.join(ext, "sidecar"))

    engine_files = kb_files = 0
    if not args.no_engine:
        engine_files = copy_tree(os.path.join(REPO, "engine"), os.path.join(ext, "sidecar", "engine"))
        # 知识库与配置默认值：engine/config.py 里的 ROOT 就是 sidecar/ 这一层
        data_src = os.path.join(REPO, "data")
        if os.path.isdir(data_src):
            kb_files = copy_tree(data_src, os.path.join(ext, "sidecar", "data"))

    py_files, py_mb = 0, 0.0
    if not args.no_python:
        src = args.python_src or os.environ.get("CTUTOR_PY_SRC") or sys.prefix
        py_files, py_mb = bundle_python(src, os.path.join(ext, "runtime"))
        if not py_files:
            print("警告：没能从 %s 抽到 Python 运行时，包里将不带解释器" % src, file=sys.stderr)

    with io.open(os.path.join(stage, "[Content_Types].xml"), "w", encoding="utf-8") as f:
        f.write(CONTENT_TYPES)
    os.makedirs(os.path.join(stage, "_rels"))
    with io.open(os.path.join(stage, "_rels", ".rels"), "w", encoding="utf-8") as f:
        f.write(RELS)
    with io.open(os.path.join(stage, "extension.vsixmanifest"), "w", encoding="utf-8") as f:
        f.write(MANIFEST.format(
            name=pkg["name"], version=version, publisher=pkg["publisher"],
            display=_xml_escape(pkg.get("displayName", pkg["name"])),
            description=_xml_escape(pkg.get("description", "")),
            keywords=",".join(pkg.get("keywords") or []),
            categories=",".join(pkg.get("categories") or []),
            engine=pkg["engines"]["vscode"],
            repo=(pkg.get("repository") or {}).get("url", ""),
        ))

    out = args.out or os.path.join(PLUGIN, "dist", "ctutor-%s.vsix" % version)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ok, detail = selfcheck(ext)
    if not ok:
        print("自检失败：%s" % detail, file=sys.stderr)
        shutil.rmtree(stage, ignore_errors=True)
        return 3
    print("自检：%s" % detail)
    if os.path.exists(out):
        os.remove(out)
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(stage):
            for f in sorted(files):
                full = os.path.join(base, f)
                rel = os.path.relpath(full, stage).replace("\\", "/")
                z.write(full, rel)
                total += 1
    size = os.path.getsize(out) / 1024.0
    print("内核：engine %d 个文件，data %d 个文件" % (engine_files, kb_files))
    if py_files:
        print("Python 运行时：%d 个文件，%.1f MB（未压缩）" % (py_files, py_mb))
    print("打包：%d 个条目，%.0f KB" % (total, size))
    print("产物：%s" % out)
    shutil.rmtree(stage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())