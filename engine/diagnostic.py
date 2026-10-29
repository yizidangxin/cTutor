# -*- coding: utf-8 -*-
"""统一诊断记录：屏蔽 MSVC / GCC 的差异，供上层知识库与解释层消费。

设计要点（来自实测）：
- MSVC 的所有编译诊断都带稳定错误码（C2146），可作为知识库主键；
- 链接错误（LNK1561）不进 SARIF，只能从文本捕获；
- GCC 的语法错误没有错误码，只有警告带 -W 选项名，因此需要消息指纹兜底。
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class FixIt:
    """编译器自带的修复建议（GCC -fdiagnostics-parseable-fixits）。"""
    file: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    replacement: str

    def __str__(self) -> str:
        return "fix-it:%s:{%d:%d-%d:%d}:'%s'" % (
            self.file, self.start_line, self.start_col,
            self.end_line, self.end_col, self.replacement)


@dataclass
class Diagnostic:
    compiler: str                 # "msvc" | "gcc"
    message: str                  # 编译器原始消息
    level: str = "error"          # error / fatal error / warning / note
    code: str = ""                # C2146 / LNK1561 / -Wunused-variable / 空
    file: str = ""
    line: int = 0
    column: int = 0
    origin: str = "sarif"         # sarif | text —— 记录来源，便于排查
    col_unit: str = ""            # 列号单位："" 按编译器默认 / "byte" / "display"
    fixits: List[FixIt] = field(default_factory=list)
    raw: str = ""

    # ---------- 便捷属性 ----------
    @property
    def is_error(self) -> bool:
        return self.level in ("error", "fatal error")

    @property
    def is_warning(self) -> bool:
        return self.level == "warning"

    @property
    def is_link_error(self) -> bool:
        return self.code.startswith("LNK")

    def sort_key(self):
        """按「先错误后警告、再按文件/行号」排序。"""
        return (0 if self.is_error else 1, self.file, self.line, self.column)

    def location(self) -> str:
        if self.file and self.line:
            return "%s:%d:%d" % (self.file, self.line, self.column)
        if self.file:
            return self.file
        return "(无位置信息)"

    def to_dict(self) -> dict:
        return {
            "compiler": self.compiler,
            "code": self.code,
            "level": self.level,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "origin": self.origin,
            "col_unit": self.col_unit,
            "fixits": [str(f) for f in self.fixits],
        }


def dedupe(diags: List[Diagnostic]) -> List[Diagnostic]:
    """按 (code, file, line, message) 去重，保留先出现的（SARIF 优先）。"""
    seen, out = set(), []
    for d in diags:
        k = (d.code, d.file, d.line, d.message.strip())
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out



