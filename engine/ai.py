# -*- coding: utf-8 -*-
"""DeepSeek 客户端（零依赖，仅用标准库 urllib）。

为什么不用 openai SDK：本项目要能离线打包给学校，多一个依赖就多一层部署风险。
DeepSeek 的接口是 OpenAI 兼容格式，用标准库直接 POST 即可。

密钥来源：环境变量 DEEPSEEK_API_KEY（或 CTUTOR_API_KEY）。
**刻意不去读取任何第三方应用的凭据库**——那样打包分发时会把别人的密钥带出去。
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"        # 便宜 4–7 倍、并发 2500，见方案第八节


class AIError(Exception):
    pass


def find_api_key() -> str:
    """按优先级找密钥：环境变量 → 配置文件（用户目录 → 项目目录）。"""
    for name in ("CTUTOR_API_KEY", "DEEPSEEK_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    try:
        from . import config as config_mod
        v = str(((config_mod.load().get("ai") or {}).get("api_key") or "")).strip()
        if v:
            return v
    except Exception:                                          # noqa: BLE001
        pass
    return ""


def key_source() -> str:
    """密钥来自哪里——出问题时最需要知道的信息。只在没有密钥时返回空串。"""
    for name in ("CTUTOR_API_KEY", "DEEPSEEK_API_KEY"):
        if os.environ.get(name, "").strip():
            return "环境变量 %s" % name
    try:
        from . import config as config_mod
        cfg = config_mod.load()
        if str(((cfg.get("ai") or {}).get("api_key") or "")).strip():
            for path, label in ((config_mod.user_config_path(), "用户配置"),
                                (config_mod.config_path(), "项目配置")):
                data = config_mod.read_json(path)
                if str(((data.get("ai") or {}).get("api_key") or "")).strip():
                    return "%s（%s）" % (label, path)
            return "配置文件"
    except Exception:                                          # noqa: BLE001
        pass
    return ""


def mask(key: str) -> str:
    """把密钥打码后再展示——任何日志、界面、报错里都只能出现这个形式。"""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "***"
    return "%s...%s（共 %d 字符）" % (key[:6], key[-4:], len(key))


def has_api_key() -> bool:
    return bool(find_api_key())


class DeepSeekClient:
    def __init__(self, api_key=None, model=DEFAULT_MODEL, base_url=DEFAULT_BASE,
                 timeout=45):
        self.api_key = api_key or find_api_key()
        if not self.api_key:
            raise AIError("未配置密钥：请设置环境变量 DEEPSEEK_API_KEY")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_usage = {}

    def chat(self, messages, json_mode=True, temperature=0.0, max_tokens=1000):
        """返回 (文本, usage)。temperature 默认 0——文献实证 0 温度解释质量更好。"""
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            # DeepSeek 的 JSON 模式要求提示词中出现 "json" 字样
            body["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise AIError("HTTP %d %s：%s" % (e.code, e.reason, detail))
        except urllib.error.URLError as e:
            raise AIError("网络不可达：%s（离线环境请勿使用 --ai）" % e.reason)
        except Exception as e:                        # noqa: BLE001
            raise AIError(str(e))

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError("返回格式异常：%s" % json.dumps(data, ensure_ascii=False)[:300])
        self.last_usage = data.get("usage") or {}
        return text, self.last_usage
