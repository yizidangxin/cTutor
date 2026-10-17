# -*- coding: utf-8 -*-
"""SARIF 2.1.0 解析。

实测结论：MSVC（cl /experimental:log <file>）与 GCC（-fdiagnostics-format=sarif-file /
-fdiagnostics-add-output=sarif:file=...）都输出 SARIF 2.1.0，因此一个解析器可通吃两家。

评审建议里点名要先验证「SARIF 版本兼容 fallback」：本模块返回解析**状态**而不是布尔值，
调用方据此决定是否回退到文本解析——SARIF 缺失、损坏、版本不认识时功能不能中断。

注意：MSVC 的链接器诊断（LNKxxxx）不会出现在 SARIF 中，需由 textparse 兜底。
"""
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from .diagnostic import Diagnostic

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_INVALID = "invalid"
STATUS_VERSION = "unsupported-version"

_DESCRIBE = {
    STATUS_MISSING: "文件未生成",
    STATUS_INVALID: "文件损坏或不是合法 JSON",
    STATUS_VERSION: "版本不受支持",
}


def describe(status: str) -> str:
    return _DESCRIBE.get(status, status)


def _uri_to_path(uri: str) -> str:
    """file:///C:/a/b.c -> C:\\a\\b.c"""
    if not uri:
        return ""
    if uri.startswith("file:"):
        p = urlparse(uri)
        path = unquote(p.path or "")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]                       # 去掉 Windows 盘符前的斜杠
        return os.path.normpath(path.replace("/", os.sep))
    return uri


def load_sarif(path: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """返回 (数据, 状态)。状态见模块常量；调用方根据状态决定是否回退。

    实测坑（只在中日韩源码上出现，英文源码永远遇不到）：
        MSVC 的 SARIF 里带一个 snippet 字段，装着源码行的**按字节截断**片段。
        源码含中文时，截断点可能落在多字节字符中间——例如
            "text": "    int x = 10\\xef\\xbc"       （三字节的 ； 被切掉最后一字节）
        于是**整份 SARIF 不是合法 UTF-8**，严格解码直接失败。
        因此这里用 replace 容错解码：JSON 结构完好，只有那半个字符变成 U+FFFD；
        而且我们本来就不用它的 snippet（源码由我们自己读，更准）。
    """
    if not path or not os.path.exists(path):
        return None, STATUS_MISSING
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, STATUS_MISSING

    text = None
    for enc in ("utf-8", "utf-8-sig"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        # 容错路径：结构可解析，个别被截断的字符降级为 U+FFFD
        text = raw.decode("utf-8", errors="replace")

    try:
        data = json.loads(text)
    except ValueError:
        return None, STATUS_INVALID
    if not isinstance(data, dict):
        return None, STATUS_INVALID
    version = str(data.get("version") or "")
    # 官方版本号形如 "2.1.0"；未来 2.x 应当仍兼容，只对非 2.x 判为不支持
    if version and not version.startswith("2."):
        return data, STATUS_VERSION
    if "runs" not in data:
        return None, STATUS_INVALID
    return data, STATUS_OK


def parse(data: Optional[Dict[str, Any]], compiler: str) -> List[Diagnostic]:
    """把 SARIF 文档转成 Diagnostic 列表。字段缺失一律容错，不抛异常。"""
    out: List[Diagnostic] = []
    if not isinstance(data, dict):
        return out
    for run in data.get("runs", []) or []:
        if not isinstance(run, dict):
            continue
        for res in run.get("results", []) or []:
            if not isinstance(res, dict):
                continue
            code = str(res.get("ruleId") or "").strip()
            # 实测：GCC 在诊断没有关联的警告选项时，会把 ruleId 写成**级别本身**
            # （"error" / "warning"），而不是规则标识符。这不是错误码，
            # 当作无码处理，否则报告里会出现 code="error" 这种噪音。
            if code.lower() in ("error", "warning", "note", "fatal error", "fatal"):
                code = ""
            # 实测：GCC 的部分警告会带一个多余的尾随等号（-Wformat= ），
            # 而知识库里登记的是规范名 -Wformat，不归一化就永远匹配不上。
            if code.endswith("="):
                code = code[:-1]
            # 实测：MSVC 的 SARIF 对**警告**会省略 level 字段（level=None），只对错误写 "error"。
            # 因此绝不能默认成 error——否则会把警告当错误报给初学者。
            # 缺失的 level 由 textparse 用编译器文本输出回填。
            level = str(res.get("level") or "").strip().lower()
            if level == "fatal":
                level = "fatal error"

            msg = ""
            m = res.get("message")
            if isinstance(m, dict):
                msg = str(m.get("text") or "").strip()
            elif isinstance(m, str):
                msg = m.strip()

            file, line, col = "", 0, 0
            locs = res.get("locations") or []
            if locs and isinstance(locs[0], dict):
                phys = locs[0].get("physicalLocation") or {}
                art = phys.get("artifactLocation") or {}
                file = _uri_to_path(str(art.get("uri") or ""))
                region = phys.get("region") or {}
                try:
                    line = int(region.get("startLine") or 0)
                    col = int(region.get("startColumn") or 0)
                except (TypeError, ValueError):
                    line = col = 0

            out.append(Diagnostic(compiler=compiler, message=msg, level=level,
                                  code=code, file=file, line=line, column=col,
                                  origin="sarif"))
    return out
