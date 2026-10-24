# -*- coding: utf-8 -*-
"""配置层：所有可变项走配置/环境变量，代码里不硬编码。

设计目标（来自评审建议第 6、7、8 条）：
  - 目录便携化：bin / data / config / log 全部相对工具根目录，不需要管理员权限、不写注册表；
  - 编译器路径与编码走配置或环境变量，绝不硬编码；
  - API Key 放用户目录或 config/ 下，并按「已经要开源」的标准管理（见 .gitignore）。

优先级：内置默认值 < config/ctutor.json < 环境变量。
"""
import json
import os
import sys
from typing import Any, Dict


def _app_root() -> str:
    """程序根目录。

    打包成 exe 之后必须以 **exe 所在目录** 为根，而不是 PyInstaller 解压出的临时目录：
      - log/ 与 build/ 要写在 exe 旁边，否则程序一关日志就没了；
      - data/ 要能被用户直接编辑，放在临时目录里改不了。
    打包脚本会把 data/ 与 bin/ 复制到 exe 同级目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _app_root()

DEFAULTS: Dict[str, Any] = {
    "compiler": {
        "prefer": "auto",              # auto | gcc | msvc
        "gcc_path": "",                # 留空=自动查找；环境变量 CTUTOR_GCC 覆盖
        "vcvars_path": "",             # 留空=自动查找；环境变量 CTUTOR_VCVARS 覆盖
        "std": "c17",
        "warnings": True,
        "timeout_seconds": 30,         # 编译超时（防病态源码卡死）
        "max_output_bytes": 2000000,   # 编译器输出上限（防内存被吃光）
    },
    "ai": {
        "enabled": False,              # 默认关闭：本地层零成本零延迟
        "api_key": "",                 # 建议放用户配置而不是项目里（面板的「设置密钥」会写用户配置）
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "timeout_seconds": 45,
        "max_retries": 1,
    },
    "log": {
        "enabled": True,
        "record_source": False,        # 默认绝不记录完整源码
        "user_id": "me",
    },
    "paths": {
        "build": "build",
        "log": "log",
        "data": "data",
        "config": "config",
        "bin": "bin",
    },
}


def read_json(path: str) -> Dict[str, Any]:
    """稳健读取 JSON 配置文件。

    实测坑：Windows 记事本、PowerShell 的 `Set-Content -Encoding UTF8` 默认都会写入
    **UTF-8 BOM**。若用 encoding="utf-8" 读取，json 会因为开头的 \\ufeff 直接解析失败，
    而调用方通常把异常吞掉——结果是「用户明明改了配置，程序却像没看见」。
    因此这里统一用 utf-8-sig（有 BOM 就去掉，没有也正常）。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def config_path() -> str:
    """项目内的配置文件（会被 .gitignore 忽略）。"""
    return os.path.join(ROOT, DEFAULTS["paths"]["config"], "ctutor.json")


def user_config_path() -> str:
    """用户目录下的配置文件：%APPDATA%\\ctutor\\config.json

    为什么要有这一份：桌面快捷方式启动的程序**继承不到**你在命令行里设的环境变量，
    所以密钥必须有地方持久保存。放在用户目录而不是项目目录，是为了：
      - 项目文件夹打包分享给别人时**不会把密钥带出去**；
      - 项目移动位置后密钥仍然有效。
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ctutor", "config.json")


def load() -> Dict[str, Any]:
    """读取配置，优先级从低到高：

        内置默认值 → 项目 config/ctutor.json → 用户目录 config.json → 环境变量
    """
    cfg = _merge(DEFAULTS, {})

    for path in (config_path(), user_config_path()):
        if os.path.exists(path):
            cfg = _merge(cfg, read_json(path))

    env = os.environ.get
    if env("CTUTOR_COMPILER"):
        cfg["compiler"]["prefer"] = env("CTUTOR_COMPILER")
    if env("CTUTOR_GCC"):
        cfg["compiler"]["gcc_path"] = env("CTUTOR_GCC")
    if env("CTUTOR_VCVARS"):
        cfg["compiler"]["vcvars_path"] = env("CTUTOR_VCVARS")
    if env("CTUTOR_STD"):
        cfg["compiler"]["std"] = env("CTUTOR_STD")
    if env("CTUTOR_AI") in ("1", "true", "on"):
        cfg["ai"]["enabled"] = True
    if env("CTUTOR_NO_LOG") in ("1", "true", "on"):
        cfg["log"]["enabled"] = False
    return cfg


def save_user_config(patch: Dict[str, Any]) -> str:
    """把配置写进用户目录（合并，不覆盖其它键）。返回写入路径。"""
    path = user_config_path()
    current = read_json(path) if os.path.exists(path) else {}
    merged = _merge(current, patch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return path


def path_of(key: str, cfg: Dict[str, Any] = None) -> str:
    """把配置里的相对目录解析成绝对路径（始终相对工具根目录，保证便携）。"""
    cfg = cfg or load()
    rel = (cfg.get("paths") or {}).get(key, key)
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    return os.path.normpath(p)


def longest_path_under(root: str, stop_at: int = 250) -> int:
    """返回 root 下最长的**文件完整路径**长度（超过 stop_at 立刻返回，不白翻几万个文件）。

    为什么需要测这个：Windows 默认路径上限是 260 字符。超了之后表现极难排查——
    解压时资源管理器会静默跳过文件，运行时则报
        Failed to load Python DLL ... LoadLibrary: 找不到指定的模块
    从这句话完全看不出跟"路径太长"有关系。
    """
    longest = 0
    try:
        for base, _dirs, files in os.walk(root):
            blen = len(base)
            for f in files:
                n = blen + 1 + len(f)
                if n > longest:
                    longest = n
                    if longest > stop_at:
                        return longest
    except OSError:
        pass
    return longest


def ensure_dirs(cfg: Dict[str, Any] = None) -> None:
    cfg = cfg or load()
    for key in ("build", "log", "data", "config", "bin"):
        try:
            os.makedirs(path_of(key, cfg), exist_ok=True)
        except OSError:
            pass
