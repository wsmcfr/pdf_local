"""Static provider presets for common hosted APIs.

Each entry only records the public connection shape (kind + base URL + a
sensible default model). Authentication and protocol details stay in the
provider adapter, so presets never bake in vendor-specific behaviour beyond
what the adapter kind already implies.
"""

from __future__ import annotations

PRESETS: list[dict[str, str]] = [
    {
        "id": "openai",
        "name": "OpenAI (Chat Completions)",
        "kind": "openai_chat",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    {
        "id": "openai-responses",
        "name": "OpenAI (Responses)",
        "kind": "openai_responses",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "kind": "openai_chat",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    {
        "id": "qwen",
        "name": "Qwen (DashScope)",
        "kind": "openai_chat",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    {
        "id": "kimi",
        "name": "Kimi (Moonshot)",
        "kind": "openai_chat",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
    },
    {
        "id": "glm",
        "name": "Zhipu GLM",
        "kind": "openai_chat",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "kind": "openai_chat",
        "base_url": "https://api.minimax.chat/v1",
        "default_model": "",
    },
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "kind": "openai_chat",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "kind": "openai_chat",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "",
    },
]

__all__ = ["PRESETS"]
