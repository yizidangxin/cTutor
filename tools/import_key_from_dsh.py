#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 DSH 凭据库导入 DeepSeek 密钥到 ctutor 的用户配置。

**为什么做成需要你显式运行的脚本，而不是自动导入**：
凭据在两个应用之间搬运，应该由你本人决定和触发，不该由工具悄悄做。

它做三件事：
  1. 只读取 ~/.dsh/.credentials.yaml 里 refs.DEEPSEEK_API_KEY 这一行；
  2. 写进 %APPDATA%\\ctutor\\config.json（不在项目目录里，打包分享不会带出去）；
  3. 顺便测一次连通性。

全程**不打印密钥内容**，只显示打码后的形式。

用法：
    python tools/import_key_from_dsh.py              # 导入并测试
    python tools/import_key_from_dsh.py --dry-run    # 只看会导入什么，不写文件
"""
import argparse
import os
import re
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

from engine import ai as ai_mod                              # noqa: E402
from engine import config as config_mod                      # noqa: E402

CRED = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")


def read_dsh_key() -> str:
    """从 DSH 凭据库的 refs 段里取出 DEEPSEEK_API_KEY。"""
    if not os.path.exists(CRED):
        return ""
    pattern = re.compile(r"^\s*DEEPSEEK_API_KEY\s*:\s*(.+?)\s*$")
    try:
        with open(CRED, "r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                m = pattern.match(line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把 DSH 里的 DeepSeek 密钥导入 ctutor")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    args = ap.parse_args(argv)

    print("DSH 凭据库：%s" % CRED)
    if not os.path.exists(CRED):
        print("找不到凭据库。请改用面板菜单 AI → 设置 DeepSeek 密钥，或 python cli.py set-key")
        return 1

    key = read_dsh_key()
    if not key:
        print("凭据库里没有 DEEPSEEK_API_KEY。")
        print("请改用面板菜单 AI → 设置 DeepSeek 密钥，或 python cli.py set-key")
        return 1

    print("找到密钥：%s" % ai_mod.mask(key))
    print("目标位置：%s" % config_mod.user_config_path())
    print("          （用户目录，不在项目里——把项目打包分享给别人不会带出密钥）")

    if args.dry_run:
        print()
        print("--dry-run：未写入任何文件。")
        return 0

    path = config_mod.save_user_config({"ai": {"api_key": key}})
    print()
    print("已写入：%s" % path)

    for name in ("CTUTOR_API_KEY", "DEEPSEEK_API_KEY"):
        if os.environ.get(name, "").strip():
            print("注意：检测到环境变量 %s，它的优先级高于配置文件。" % name)

    print()
    print("测试连通性…")
    try:
        client = ai_mod.DeepSeekClient()
        text, usage = client.chat([{"role": "user", "content": "请只回答两个字：正常"}],
                                  json_mode=False, max_tokens=200)
        print("连通 ✓ 模型 %s，返回 %r" % (client.model, text.strip()[:20]))
        print()
        print("完成。现在在面板里勾上「AI 补充」即可——")
        print("它只在本地知识库没收录某个错误时才会调用，命中时完全不联网、不花钱。")
        return 0
    except ai_mod.AIError as e:
        print("连通失败：%s" % e)
        print("密钥已保存，但请检查网络或密钥是否有效。")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
