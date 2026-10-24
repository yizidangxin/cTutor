# -*- coding: utf-8 -*-
"""守卫层：脱敏、schema 校验、答案泄漏检测、位置锚定。

这一层是「不给答案」这条硬约束的真正执行者。文献依据：
- CodeHelp 实测模型 **48%** 的情况下无视「不要给答案」的指令；
- DCC Sidekick 因此加了**二次改写守卫**，把含代码块的回答改写成不含代码的措辞。
结论：不能靠提示词祈祷，必须有工程手段。
"""
import re
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------- 脱敏
# 学生代码里可能带本机路径、邮箱、甚至误粘的密钥；发送前一律剥离（照抄 c-sensei 的做法）
_PATTERNS = [
    (re.compile(r"[A-Za-z]:\\[^\s\"'<>|]+"), "<本机路径>"),
    (re.compile(r"/(?:home|Users)/[^\s\"'<>|]+"), "<本机路径>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<邮箱>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "<疑似密钥>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "<疑似长串>"),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


# ---------------------------------------------------------------- schema 校验
REQUIRED = {
    "one_line": str,
    "confidence": str,
}
OPTIONAL_LISTS = ("why", "how_to_fix", "pitfalls")
OPTIONAL_STR = ("concept", "refuse_reason")


def validate_payload(obj: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """返回 (是否通过, 问题列表)。字段缺失或类型不符即视为不合格。"""
    problems: List[str] = []
    if not isinstance(obj, dict):
        return False, ["顶层不是对象"]
    for key, typ in REQUIRED.items():
        if key not in obj:
            problems.append("缺少字段 %s" % key)
        elif not isinstance(obj[key], typ):
            problems.append("字段 %s 类型应为 %s" % (key, typ.__name__))
    if not str(obj.get("one_line", "")).strip():
        problems.append("one_line 为空")
    for key in OPTIONAL_LISTS:
        if key in obj and not isinstance(obj[key], list):
            problems.append("字段 %s 应为列表" % key)
    for key in OPTIONAL_STR:
        if key in obj and not isinstance(obj[key], str):
            problems.append("字段 %s 应为字符串" % key)
    conf = str(obj.get("confidence", "")).lower()
    if conf and conf not in ("high", "medium", "low"):
        problems.append("confidence 取值非法：%s" % conf)
    return (not problems), problems


# ---------------------------------------------------------------- 答案泄漏守卫
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_C_LIKE = re.compile(r"(#\s*include|int\s+main\s*\(|\bprintf\s*\(|\bscanf\s*\(|;\s*$|\{|\})", re.M)


def _looks_like_code(text: str) -> bool:
    """判断一段文本是否构成「可复制的代码解法」。

    判据（保守）：
      - 含 markdown 代码围栏；或
      - 含 #include / int main( 这类整段程序特征；或
      - 含换行且同时出现分号结尾与花括号（多条语句组成的块）。
    单行的「把分号补上」不会命中。
    """
    if not text:
        return False
    if "```" in text:
        return True
    if re.search(r"#\s*include|int\s+main\s*\(", text):
        return True
    if "\n" in text.strip():
        hits = len(_C_LIKE.findall(text))
        if hits >= 2:
            return True
    return False


def sanitize_fix(text: str) -> Tuple[str, bool]:
    """剥离代码解法，返回 (净化后的文字, 是否发生了剥离)。"""
    if not text:
        return text, False
    if not _looks_like_code(text):
        return text, False
    # 去掉围栏代码块，只保留说明文字
    cleaned = _CODE_FENCE.sub("", text)
    # 若去掉后几乎没剩下什么，就退化成一句通用说明
    cleaned = cleaned.strip()
    if len(cleaned) < 8:
        cleaned = "（这条修改涉及整段代码，为避免直接给出答案，此处只描述位置与思路）"
    else:
        # 去掉明显是代码的行
        kept = [ln for ln in cleaned.split("\n")
                if ln.strip() and not _C_LIKE.search(ln)]
        if kept:
            # 用空格拼：以前是 "" .join，两行说明会被粘成一句
            #（"少了分号" + "把它补上" → "少了分号把它补上"），读起来像病句。
            cleaned = " ".join(ln.strip() for ln in kept).replace("  ", " ").strip()
        else:
            cleaned = "（这条修改涉及整段代码，为避免直接给出答案，此处只描述位置与思路）"
    return cleaned, True


def apply_guard(obj: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """对模型输出整体施加守卫。返回 (净化后的对象, 守卫动作记录)。"""
    notes: List[str] = []
    out = dict(obj)
    for key in OPTIONAL_LISTS:
        items = out.get(key)
        if isinstance(items, list):
            new_items = []
            for it in items:
                if not isinstance(it, str):
                    continue
                clean, stripped = sanitize_fix(it)
                if stripped:
                    notes.append("已剥离 %s 中的代码解法" % key)
                new_items.append(clean.strip())
            out[key] = new_items[:4]
    if isinstance(out.get("concept"), str):
        clean, stripped = sanitize_fix(out["concept"])
        if stripped:
            notes.append("已剥离 concept 中的代码解法")
        out["concept"] = clean.strip()
    # 位置字段一律丢弃：位置只信编译器
    for bad in ("line", "lines", "column", "file", "filename", "location"):
        if bad in out:
            out.pop(bad)
            notes.append("已丢弃模型返回的位置字段 %s" % bad)
    return out, sorted(set(notes))


def confidence_of(obj: Dict[str, Any]) -> str:
    return str(obj.get("confidence", "")).lower() or "medium"


def should_degrade(obj: Dict[str, Any]) -> bool:
    """是否应降级为「只给定位不给解释」。

    条件：低置信度且模型自己说不清（refuse_reason 非空）。
    """
    return confidence_of(obj) == "low" and bool(str(obj.get("refuse_reason", "")).strip())
