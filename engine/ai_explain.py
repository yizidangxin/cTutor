# -*- coding: utf-8 -*-
"""AI 解释编排：调用模型 → 解析 → 校验 → 重试 → 降级 → 守卫。

严格遵循方案里定下的分层路由：
    **只有在本地知识库未命中时才调用模型。** 命中时零成本、零延迟、零幻觉。
模型输出只用来填补「原因 / 改法 / 知识点」，**位置永远来自编译器**。
"""
from typing import Any, Dict, List, Optional

from . import guard, prompt
from .ai import AIError, DeepSeekClient
from .explain import Explanation


class AIResult:
    def __init__(self, ok=False, payload=None, degraded=False, notes=None,
                 warnings=None, usage=None, attempts=0, error=""):
        self.ok = ok
        self.payload: Dict[str, Any] = payload or {}
        self.degraded = degraded
        self.notes: List[str] = notes or []          # 守卫动作（剥离了代码等）
        self.warnings: List[str] = warnings or []    # 重试/降级原因
        self.usage: Dict[str, Any] = usage or {}
        self.attempts = attempts
        self.error = error

    def summary(self) -> str:
        if not self.ok:
            return "调用失败：%s" % self.error
        bits = ["尝试 %d 次" % self.attempts]
        if self.usage:
            bits.append("tokens in=%s out=%s" % (self.usage.get("prompt_tokens", "?"),
                                                 self.usage.get("completion_tokens", "?")))
            hit = self.usage.get("prompt_cache_hit_tokens")
            miss = self.usage.get("prompt_cache_miss_tokens")
            if hit is not None and miss is not None:
                # 缓存命中价 $0.003/M，未命中 $0.15/M——相差 50 倍，值得让学生/老师看见
                bits.append("上下文缓存：命中 %s / 未命中 %s" % (hit, miss))
        if self.degraded:
            bits.append("已降级为只给定位")
        bits += self.notes
        return "，".join(bits)


def code_context(exp: Explanation, max_lines: int = 9) -> str:
    """把出错行连同上下文拼成给模型看的片段，出错行用 --> 标出。"""
    if not exp.snippet:
        return ""
    out = []
    for ln in exp.snippet[:max_lines]:
        head = ln.split("|", 1)[0].strip()
        try:
            n = int(head)
        except ValueError:
            n = -1
        out.append(("--> " if n == exp.diag.line else "    ") + ln)
    return "\n".join(out)


def enrich(exp: Explanation, client: Optional[DeepSeekClient] = None,
           max_retry: int = 1) -> AIResult:
    """为一条未命中知识库的诊断获取 AI 讲解。"""
    if client is None:
        try:
            client = DeepSeekClient()
        except AIError as e:
            return AIResult(ok=False, error=str(e))

    ctx = code_context(exp)
    ctx = guard.redact(ctx)          # 脱敏后再发送
    messages = prompt.build_messages(exp.diag, ctx)
    warnings: List[str] = []
    usage: Dict[str, Any] = {}
    attempts = 0

    for attempt in range(max_retry + 1):
        attempts += 1
        try:
            text, usage = client.chat(messages)
        except AIError as e:
            return AIResult(ok=False, error=str(e), attempts=attempts,
                            warnings=warnings, usage=usage)

        try:
            obj = prompt.parse_json_payload(text)
        except ValueError as e:
            warnings.append("第 %d 次输出不是合法 json（%s）" % (attempt, e))
            if attempt < max_retry:
                messages = prompt.build_retry_messages(messages)
                continue
            return AIResult(ok=False, error="json 解析失败", attempts=attempts,
                            warnings=warnings, usage=usage)

        ok, problems = guard.validate_payload(obj)
        if not ok:
            warnings.append("第 %d 次输出不符合 schema：%s" % (attempt, "；".join(problems)))
            if attempt < max_retry:
                messages = prompt.build_retry_messages(messages)
                continue
            # 重试用尽仍不合格 → 降级：只保留能安全使用的一句话
            one = str(obj.get("one_line", "")).strip()
            if one:
                payload, notes = guard.apply_guard({"one_line": one, "confidence": "low"})
                return AIResult(ok=True, payload=payload, degraded=True,
                                notes=notes, warnings=warnings, usage=usage,
                                attempts=attempts)
            return AIResult(ok=False, error="schema 校验失败且无法降级",
                            attempts=attempts, warnings=warnings, usage=usage)
        break

    payload, notes = guard.apply_guard(obj)

    # 降级：模型自己说信息不足时，不要硬给解释
    if guard.should_degrade(payload):
        reason = str(payload.get("refuse_reason", "")).strip()
        warnings.append("模型表示信息不足：%s" % reason)
        keep = {"one_line": str(payload.get("one_line", "")).strip(),
                "confidence": "low", "refuse_reason": reason}
        return AIResult(ok=True, payload=keep, degraded=True, notes=notes,
                        warnings=warnings, usage=usage, attempts=attempts)

    return AIResult(ok=True, payload=payload, notes=notes, warnings=warnings,
                    usage=usage, attempts=attempts)
