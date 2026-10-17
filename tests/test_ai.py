#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 层离线测试：用假客户端验证守卫逻辑，不联网、不产生费用。

这一步真正的风险不是「能不能调通接口」，而是：
  - 提示词前缀是否字节稳定（决定上下文缓存能不能命中，价格差 50 倍）
  - 模型输出不合法时会不会把垃圾展示给学生
  - 模型给出完整代码解法时会不会照单全收（文献实测 48% 的情况会违规）
  - 模型编造行号时会不会覆盖编译器的真实位置
这些都必须有测试，而不能靠"提示词里写了不许"。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import ai_explain, explain, guard, prompt       # noqa: E402
from engine.ai import AIError                                # noqa: E402
from engine.diagnostic import Diagnostic                     # noqa: E402
from engine.kb import KnowledgeBase                          # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "　→ " + str(detail)))


class FakeClient:
    """假客户端：按脚本返回，记录收到的 messages。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0) if self.replies else "{}"
        if isinstance(reply, Exception):
            raise reply
        return reply, {"prompt_tokens": 120, "completion_tokens": 60}


def make_exp(code="C9999", message="测试用报错", line=0):
    diag = Diagnostic(compiler="msvc", message=message, code=code,
                      level="error", file="", line=line)
    return explain.build(diag, KnowledgeBase([]))


GOOD = json.dumps({
    "one_line": "变量没有定义就被使用了。",
    "why": ["C 语言要求先定义后使用。"],
    "how_to_fix": ["在第 3 行补上变量定义。"],
    "pitfalls": ["拼写错误也会报这个错。"],
    "concept": "声明与定义的区别。",
    "confidence": "high",
    "refuse_reason": "",
}, ensure_ascii=False)

# ---------------------------------------------------------------- 1. 前缀稳定性
sha1 = prompt.stable_prefix_sha()
m1 = prompt.build_messages(make_exp(message="错误A").diag, "code A")
m2 = prompt.build_messages(make_exp(code="C1111", message="完全不同的错误B").diag, "code B")
check("系统提示是 messages[0] 且与 STABLE_PREFIX 完全一致",
      m1[0]["content"] == prompt.STABLE_PREFIX,
      m1[0]["content"][:40])
check("前缀 sha256 不随输入变化（缓存友好的前提）",
      sha1 == prompt.stable_prefix_sha() and m1[0]["content"] == m2[0]["content"])
check("可变内容全部在最后一条消息里",
      "错误A" in m1[-1]["content"] and "错误A" not in m1[0]["content"]
      and "错误B" in m2[-1]["content"] and "错误B" not in m2[0]["content"])
check("重试消息不改变前缀（仍可命中缓存）",
      prompt.build_retry_messages(m1)[0]["content"] == prompt.STABLE_PREFIX)

# ---------------------------------------------------------------- 2. JSON 容错解析
cases = [
    ('{"a":1}', True),
    ('```json\n{"a":1}\n```', True),
    ('```\n{"a":1}\n```', True),
    ('好的，这是结果：{"a":1} 希望有帮助', True),
    ('not json at all', False),
]
for text, should_ok in cases:
    try:
        obj = prompt.parse_json_payload(text)
        ok = obj.get("a") == 1
    except ValueError:
        ok = False
    check("JSON 容错解析：%r" % text[:26], ok == should_ok, ok)

# ---------------------------------------------------------------- 3. schema 校验
good_obj = json.loads(GOOD)
check("合法输出通过校验", guard.validate_payload(good_obj)[0])
bad_cases = [
    ({}, "缺字段"),
    ({"one_line": "", "confidence": "high"}, "空 one_line"),
    ({"one_line": "x", "confidence": "very-high"}, "confidence 取值非法"),
    ({"one_line": "x", "confidence": "high", "why": "不是列表"}, "why 类型错误"),
]
for obj, label in bad_cases:
    ok, probs = guard.validate_payload(obj)
    check("schema 拒绝：%s" % label, not ok, probs)

# ---------------------------------------------------------------- 4. 正常调用
client = FakeClient([GOOD])
res = ai_explain.enrich(make_exp(), client)
check("正常调用：ok 且 attempts=1", res.ok and res.attempts == 1, res.summary())
check("正常调用：token 用量被记录", res.usage.get("prompt_tokens") == 120, res.usage)

# ---------------------------------------------------------------- 5. 非法输出 → 重试 → 成功
client = FakeClient(["这不是 json", GOOD])
res = ai_explain.enrich(make_exp(), client)
check("首次非法 JSON → 重试后成功", res.ok and res.attempts == 2, res.summary())
check("重试确实发生了第 2 次调用", len(client.calls) == 2, len(client.calls))

# ---------------------------------------------------------------- 6. 不合法输出的两条路径
# 6a. 是合法 JSON 但不符合 schema → 降级：保留可安全使用的一句话
client = FakeClient(['{"one_line":"只有一句话但缺 confidence"}',
                     '{"one_line":"仍然只有一句话"}'])
res = ai_explain.enrich(make_exp(), client)
check("两次 schema 不合格 → 降级但保留可用结论",
      res.ok and res.degraded and res.payload.get("one_line"), res.summary())
check("降级后 confidence 被置为 low", res.payload.get("confidence") == "low", res.payload)

# 6b. 完全不是 JSON → **失败关闭**，绝不展示未经校验的自由文本
client = FakeClient(["这不是 json，是一段散文", "仍然不是 json"])
res = ai_explain.enrich(make_exp(), client)
check("两次都不是 JSON → 失败关闭，不展示未经校验的文本",
      (not res.ok) and "json" in res.error.lower(), res.summary())

# ---------------------------------------------------------------- 7. 低置信度+信息不足 → 降级
low = json.dumps({"one_line": "信息不足，无法判断。", "confidence": "low",
                  "refuse_reason": "需要看到完整的函数定义", "why": ["猜测A"]},
                 ensure_ascii=False)
client = FakeClient([low])
res = ai_explain.enrich(make_exp(), client)
check("模型自述信息不足 → 降级为只给定位",
      res.ok and res.degraded and "why" not in res.payload, res.summary())

# ---------------------------------------------------------------- 8. 答案泄漏守卫
leaky = json.dumps({
    "one_line": "初始化有问题。",
    "why": ["没有初始化"],
    "how_to_fix": ["把这段替换掉：\n#include <stdio.h>\nint main(void) {\n    int a = 0;\n    printf(\"%d\", a);\n    return 0;\n}"],
    "confidence": "high", "refuse_reason": "",
}, ensure_ascii=False)
client = FakeClient([leaky])
res = ai_explain.enrich(make_exp(), client)
fix_text = " ".join(res.payload.get("how_to_fix", []))
check("完整代码解法被剥离", "#include" not in fix_text and "int main" not in fix_text,
      fix_text[:80])
check("守卫动作被记录", any("剥离" in n for n in res.notes), res.notes)

check("单行动作不会被误判为代码", guard.sanitize_fix("把第 3 行末尾的分号补上")[1] is False)
check("围栏代码块被识别", guard.sanitize_fix("这样写：\n```c\nint x = 1;\n```")[1] is True)

# ---------------------------------------------------------------- 9. 位置锚定
withpos = json.dumps({"one_line": "有问题。", "confidence": "high",
                      "line": 999, "file": "C:\\fake\\path.c", "column": 42},
                     ensure_ascii=False)
client = FakeClient([withpos])
res = ai_explain.enrich(make_exp(), client)
check("模型返回的位置字段被丢弃",
      not any(k in res.payload for k in ("line", "file", "column")), res.payload)
check("丢弃动作被记录", any("位置字段" in n for n in res.notes), res.notes)

# ---------------------------------------------------------------- 10. 脱敏
raw = r'错误在 C:\Users\alice\Documents\secret\hw.c，联系 a@b.com，key=sk-abcdefghijklmnopqrstuvwx'
red = guard.redact(raw)
check("本机绝对路径被脱敏", "C:\\Users\\Rui" not in red, red)
check("邮箱被脱敏", "a@b.com" not in red, red)
check("疑似密钥被脱敏", "sk-abcdefghijklmnopqrstuvwx" not in red, red)

ctx_diag = Diagnostic(compiler="msvc", message="e", code="C1", level="error",
                      file="", line=0)
exp_send = explain.build(ctx_diag, KnowledgeBase([]))
exp_send.snippet = ["   1 | int a = 1;"]
exp_send.line_text = "int a = 1;"
sent = FakeClient([GOOD])
ai_explain.enrich(exp_send, sent)
check("发送前确实做了脱敏（代码上下文进入 user 消息）",
      "int a = 1;" in sent.calls[0][-1]["content"], sent.calls[0][-1]["content"][:60])

# ---------------------------------------------------------------- 11. 网络错误
client = FakeClient([AIError("网络不可达：测试")])
res = ai_explain.enrich(make_exp(), client)
check("网络异常被捕获为失败而非崩溃", (not res.ok) and "网络不可达" in res.error, res.summary())

# ---------------------------------------------------------------- 12. 渲染：AI 来源标注
exp = make_exp()
exp.ai = json.loads(GOOD)
exp.ai_meta = "尝试 1 次"
from engine import render as render_mod                      # noqa: E402
text = render_mod.render(exp, depth=5)
check("AI 来源在输出里被明确标注", "由 AI 生成" in text, text[:200])
check("位置层不含 AI 标注", "【2 定位】" in text)
check("AI 元信息被展示", "AI 补充：尝试 1 次" in text)

# 知识库优先于 AI
exp2 = make_exp()
exp2.entry = {"title": "人工条目", "one_line": "人工写的结论",
              "why": ["人工原因"], "how_to_fix": ["人工改法"]}
exp2.ai = json.loads(GOOD)
text2 = render_mod.render(exp2, depth=5)
check("知识库命中时优先使用人工内容",
      "人工写的结论" in text2 and "变量没有定义就被使用了" not in text2)

n_fail = sum(1 for _, ok in results if not ok)
print()
print("=" * 66)
print("AI 层离线测试：%d 项，通过 %d，失败 %d"
      % (len(results), len(results) - n_fail, n_fail))
print("=" * 66)
raise SystemExit(0 if n_fail == 0 else 1)
