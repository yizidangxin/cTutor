# -*- coding: utf-8 -*-
"""上次进度（会话）的保存与恢复。

需求：关掉程序，下次打开**接着写**，不用重新找文件。

具体要记住三样东西：
  1. 开过哪些文件（以及各自用的编码）
  2. 当前停在哪个标签
  3. **还没保存的改动内容** —— 这是关键。如果只记文件名，学生写了一半
     忘了存就关掉，下次打开文件里还是旧的，那段代码等于白写。

所以：有改动的文档把文本一起存进会话里；没改动的只存路径，下次从磁盘重读
（这样文件被别的程序改过也不会读到过期的内容）。

几条稳妥性设计：
  * 会话只写进 `%APPDATA%\\ctutor\\session.json`，**不写项目目录**，
    这样项目文件夹打包发给别人时不会带上你的编辑状态。
  * 写入用"先写临时文件再替换"，中途断电不会留下半个 JSON。
  * 文件读不出来、JSON 坏了、字段缺失 —— 一律当作"没有上次进度"，
    退回到示例代码，**绝不因为一个坏会话文件而打不开程序**。
  * 文本总量有上限，超了就只保路径不保文本，避免会话文件无限膨胀。
"""
import json
import os
from typing import Any, Dict, List, Optional

SESSION_SCHEMA = "ctutor/session/1"
MAX_DOCS = 20                       # 最多记这么多标签
MAX_TEXT_BYTES = 1_500_000          # 会话里最多存这么多文本（约 1.5 MB）


def session_path(cfg: Optional[Dict[str, Any]] = None) -> str:
    """会话文件位置：%APPDATA%\\ctutor\\session.json"""
    from . import config as config_mod
    # 和 config.json 放同一个目录
    return os.path.join(os.path.dirname(config_mod.user_config_path()), "session.json")


def save(docs: List[Dict[str, Any]], active: int,
         cfg: Optional[Dict[str, Any]] = None) -> str:
    """保存会话。docs 里每项：path / encoding / text / modified / label。

    返回写入路径（失败时返回空串）。
    """
    keep: List[Dict[str, Any]] = []
    budget = MAX_TEXT_BYTES

    for d in (docs or [])[:MAX_DOCS]:
        if not isinstance(d, dict):
            continue
        path = d.get("path") or ""
        text = d.get("text") or ""
        modified = bool(d.get("modified"))
        item: Dict[str, Any] = {
            "path": path,
            "encoding": d.get("encoding") or "mbcs",
            "label": d.get("label") or "未命名",
            "modified": modified,
        }
        if not path and not text:
            continue                                  # 空文档没必要记
        # 只有"未保存的改动"才值得把文本存进来；没改动的下次从磁盘读更可靠
        if modified and text:
            raw = text.encode("utf-8", errors="replace")
            if len(raw) <= budget:
                item["text"] = text
                budget -= len(raw)
            else:
                item["text"] = ""
        keep.append(item)

    if not keep:
        clear(cfg)
        return ""

    payload = {
        "schema": SESSION_SCHEMA,
        "active": max(0, min(int(active or 0), len(keep) - 1)),
        "docs": keep,
    }
    p = session_path(cfg)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, p)                            # 原子替换
        return p
    except OSError:
        return ""


def load(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """读取会话。任何异常都返回空结构，不让坏文件挡住启动。"""
    empty: Dict[str, Any] = {"schema": SESSION_SCHEMA, "active": 0, "docs": []}
    p = session_path(cfg)
    if not os.path.exists(p):
        return empty
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    if data.get("schema") != SESSION_SCHEMA:
        return empty
    docs = data.get("docs")
    if not isinstance(docs, list):
        return empty

    out: List[Dict[str, Any]] = []
    for d in docs[:MAX_DOCS]:
        if not isinstance(d, dict):
            continue
        path = d.get("path")
        text = d.get("text")
        out.append({
            "path": path if isinstance(path, str) else "",
            "encoding": d.get("encoding") if isinstance(d.get("encoding"), str) else "mbcs",
            "label": d.get("label") if isinstance(d.get("label"), str) else "未命名",
            "modified": bool(d.get("modified")),
            "text": text if isinstance(text, str) else "",
        })
    try:
        active = int(data.get("active") or 0)
    except (TypeError, ValueError):
        active = 0
    return {"schema": SESSION_SCHEMA, "active": active, "docs": out}


def clear(cfg: Optional[Dict[str, Any]] = None) -> None:
    p = session_path(cfg)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass
