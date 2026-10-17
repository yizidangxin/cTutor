#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一入口。

    不带参数  →  启动图形面板（双击 exe 的默认行为）
    带子命令  →  走命令行（check / fix / doctor / kb / pending / set-key）

打包成 exe 时以本文件为入口，这样同一次构建既能双击用、也能命令行用。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

CLI_COMMANDS = {"check", "fix", "doctor", "kb", "pending", "set-key", "run"}


def _usage() -> str:
    return """ctutor — C 语言报错解释器

用法：
  ctutor                    启动图形面板（双击即可）
  ctutor 你的代码.c          打开该文件并启动面板
  ctutor check 你的代码.c    命令行：分层解释报错
  ctutor check 你的代码.c -o report.json
  ctutor fix 你的代码.c      [实验性] 确定性修复并重新编译验证
  ctutor kb                  知识库统计与 Top-20 命中率
  ctutor pending             待补清单（未收录的错误码）
  ctutor doctor              环境与配置自检
  ctutor set-key             设置 DeepSeek 密钥

面板里也可以用：F5 检查、F1 帮助、输入前几个字母有代码补全。
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    if argv and argv[0] in ("-V", "--version"):
        try:
            from cli import TOOL_VERSION
        except Exception:                                       # noqa: BLE001
            TOOL_VERSION = "?"
        print("ctutor %s" % TOOL_VERSION)
        return 0

    if argv and argv[0] in CLI_COMMANDS:
        from cli import main as cli_main
        return cli_main(argv)

    from panel import main as panel_main
    return panel_main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:                                       # noqa: BLE001
        import traceback
        try:
            from engine import config as config_mod
            log_dir = config_mod.path_of("log")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "startup_crash.log"), "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:                                       # noqa: BLE001
            pass
        raise
