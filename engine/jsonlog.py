# -*- coding: utf-8 -*-
"""结构化日志（JSONL）。

评审建议第 10 条：日志用结构化 JSONL，**默认不记录完整源码**，user_id 先填 "me"。

两个用途：
1. `log/ctutor.jsonl`   —— 每次运行的事件流水（用于统计与排查）
2. `log/unmatched.jsonl` —— **未命中知识库的错误码清单**，也就是「待补清单」。
   评审建议里那条很重要：解释正确率别拍脑袋，把"不确定"的条目记下来当 backlog，
   自用几周后按真实频次补知识库，比一次性猜 80 条更有效。
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

from . import config as config_mod

_MAX_FIELD = 400


def _log_dir(cfg: Dict[str, Any]) -> str:
    d = config_mod.path_of("log", cfg)
    os.makedirs(d, exist_ok=True)
    return d


def _append(filename: str, record: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    if not (cfg.get("log") or {}).get("enabled", True):
        return
    try:
        path = os.path.join(_log_dir(cfg), filename)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass          # 日志失败绝不影响主流程


def _base(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user_id": (cfg.get("log") or {}).get("user_id", "me"),
    }


def event(name: str, cfg: Optional[Dict[str, Any]] = None, **fields) -> None:
    cfg = cfg or config_mod.load()
    rec = _base(cfg)
    rec["event"] = name
    for k, v in fields.items():
        if isinstance(v, str) and len(v) > _MAX_FIELD:
            v = v[:_MAX_FIELD] + "…"
        rec[k] = v
    _append("ctutor.jsonl", rec, cfg)


def unmatched(diag, cfg: Optional[Dict[str, Any]] = None,
              source_basename: str = "", extra: str = "") -> None:
    """记录一条未命中知识库的诊断 —— 这是知识库的增长来源。"""
    cfg = cfg or config_mod.load()
    rec = _base(cfg)
    rec.update({
        "event": "unmatched",
        "compiler": diag.compiler,
        "code": diag.code or "(无错误码)",
        "level": diag.level,
        "message": diag.message,
        "file": source_basename,
        "line": diag.line,
        "origin": diag.origin,
    })
    # 默认不记录源码内容；只有显式打开 record_source 才带上出错行
    if (cfg.get("log") or {}).get("record_source"):
        rec["source_line"] = extra
    _append("unmatched.jsonl", rec, cfg)


def read_unmatched(cfg: Optional[Dict[str, Any]] = None,
                   limit: int = 200) -> List[Dict[str, Any]]:
    cfg = cfg or config_mod.load()
    path = os.path.join(config_mod.path_of("log", cfg), "unmatched.jsonl")
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        # utf-8-sig：日志首行若带 BOM（被记事本另存过），不应导致整份日志读不出来
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out[-limit:]


def pending_summary(cfg: Optional[Dict[str, Any]] = None) -> List[tuple]:
    """按错误码聚合「待补清单」，返回 [(错误码, 出现次数, 最近一条消息)]，按次数降序。"""
    agg: Dict[str, Dict[str, Any]] = {}
    for rec in read_unmatched(cfg):
        key = rec.get("code") or "(无错误码)"
        slot = agg.setdefault(key, {"count": 0, "message": "", "compiler": ""})
        slot["count"] += 1
        slot["message"] = rec.get("message", "")
        slot["compiler"] = rec.get("compiler", "")
    return sorted(((k, v["count"], v["message"]) for k, v in agg.items()),
                  key=lambda t: -t[1])
