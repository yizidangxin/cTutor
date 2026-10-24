# -*- coding: utf-8 -*-
"""L0 中文知识库：按「编译器 + 错误码 / 消息指纹」匹配手写中文解释。

设计依据（调研结论）：
- MSVC 的所有编译诊断都有稳定错误码，是知识库最可靠的索引键；
- GCC 的语法错误没有错误码，只有警告带 -W 选项名，因此需要消息指纹兜底；
- 因此采用双键匹配：先精确匹配错误码，再退回消息子串匹配。

所有条目均为人工撰写，因此正确率是 100%——这正是 Leinonen 等人建议的
human-in-the-loop 两层架构中「确定层」的落地。
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

from . import config as config_mod
from .diagnostic import Diagnostic


def _normalize(s: str) -> str:
    """去掉引号与标点、压缩空白，便于跨措辞比较。"""
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zA-Z ]+", " ", s.lower())).strip()


FILES = {"msvc": "msvc.json", "gcc": "gcc.json"}


def kb_dir() -> str:
    """知识库目录：data/kb（相对工具根目录，便携）。

    评审建议第 9 条：错误码映射表必须写成数据文件而不是 if-else——
    这是唯一长期增值的资产。因此它放在 data/ 下，可以被单独备份、分享、替换。
    """
    return os.path.join(config_mod.path_of("data"), "kb")


class KnowledgeBase:
    def __init__(self, entries: Optional[List[Dict[str, Any]]] = None):
        self.entries: List[Dict[str, Any]] = entries or []
        self._by_code: Dict[str, List[Dict[str, Any]]] = {}
        # 消息指纹预先摊平：lookup() 每次检查都要按指纹扫一遍全库，
        # covers() 更是要把每条指纹归一化后再比。这些数据装载后就不会变，
        # 因此只在构造时算一次——以前每次查询都重新 .get() 加重新跑正则。
        self._patterns: List[tuple] = []            # (原文, 条目, 该条目是否带错误码)
        self._norm_patterns: List[str] = []         # 归一化后的指纹（covers 用）
        for e in self.entries:
            for code in [e.get("code")] + list(e.get("aliases") or []):
                if code:
                    self._by_code.setdefault(code.upper(), []).append(e)
            has_code = bool(e.get("code"))
            for p in e.get("match_message") or []:
                if not p:
                    continue
                self._patterns.append((p, e, has_code))
                np = _normalize(p)
                if np:
                    self._norm_patterns.append(np)

    # ---------- 装载 ----------
    @classmethod
    def load(cls, compiler: str, kb_dir_path: Optional[str] = None) -> "KnowledgeBase":
        fname = FILES.get(compiler)
        base = kb_dir_path or kb_dir()
        entries: List[Dict[str, Any]] = []
        for name in filter(None, ("common.json", fname)):
            path = os.path.join(base, name)
            if not os.path.exists(path):
                continue
            data = config_mod.read_json(path)      # utf-8-sig：容忍记事本写入的 BOM
            for e in data.get("entries", []):
                if e.get("compiler") in (None, compiler, "any"):
                    entries.append(e)
        return cls(entries)

    # ---------- 查询 ----------
    def lookup(self, diag: Diagnostic) -> Optional[Dict[str, Any]]:
        """双键匹配：错误码优先，消息指纹兜底。

        重要设计决定：match_message 只用于**在同一个错误码的多个条目之间挑选**，
        绝不用于否决唯一的条目。原因是实测发现 MSVC 的中文措辞与预期不完全一致
        （例如 C1075 的消息是「“{”: 未找到匹配令牌」，而不是「左大括号」），
        若用消息模式做否决，反而会把本来收录了的错误码判成「未收录」。
        """
        # 1) 错误码精确匹配
        if diag.code:
            candidates = self._by_code.get(diag.code.upper(), [])
            if len(candidates) == 1:
                return candidates[0]                      # 唯一候选，直接采用
            for e in candidates:                          # 多候选用消息细化
                pats = e.get("match_message")
                if pats and any(p and p in diag.message for p in pats):
                    return e
            if candidates:
                return candidates[0]                      # 都不匹配则退回首条
        # 2) 消息指纹匹配（GCC 语法错误无错误码；或错误码未收录）
        for p, e, has_code in self._patterns:
            if has_code and diag.code:
                continue          # 已有错误码时不跨码匹配，避免张冠李戴
            if p in diag.message:
                return e
        return None

    def stats(self) -> Dict[str, int]:
        return {
            "entries": len(self.entries),
            "codes": len(self._by_code),
            "with_pattern": sum(1 for e in self.entries if e.get("match_message")),
        }

    def covers(self, token: str) -> bool:
        """判断某个高频项是否已被收录。

        token 可能是 MSVC 错误码（C2146），也可能是 GCC 的消息片段
        （expected ';' before）。比较前先归一化：去掉引号与标点，
        否则 "else without a previous if" 与 "'else' without a previous 'if'"
        会被误判为不匹配，指标就会虚报缺口。
        """
        if not token:
            return False
        if token.upper() in self._by_code:
            return True
        norm = _normalize(token)
        for np in self._norm_patterns:
            if np in norm or norm in np:
                return True
        return False
