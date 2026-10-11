import os
from urllib.parse import urlsplit

import httpx
from langchain_openai import ChatOpenAI


class ProviderNotConfigured(RuntimeError):
    pass


def provider_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL") and os.getenv("LLM_BASE_URL"))


class ZaiChatOpenAI(ChatOpenAI):
    """Keep Z.AI's documented token-limit field at the actual HTTP boundary.

    ChatOpenAI normalizes max_tokens to OpenAI's max_completion_tokens even
    for compatible providers. Z.AI's API documents max_tokens instead.
    """
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload


def create_model(*, http_client: httpx.Client | None = None, http_async_client: httpx.AsyncClient | None = None):
    if not provider_configured():
        raise ProviderNotConfigured("Configure LLM_BASE_URL, LLM_MODEL and LLM_API_KEY")
    # Both Z.AI general API and OpenRouter implement this chat/tool protocol.
    options = {}
    if os.environ["LLM_MODEL"].lower().startswith("glm-5."):
        options["reasoning_effort"] = os.getenv("LLM_REASONING_EFFORT", "low")
    if http_client is not None:
        options["http_client"] = http_client
    if http_async_client is not None:
        options["http_async_client"] = http_async_client
    adapter = ZaiChatOpenAI if urlsplit(os.environ["LLM_BASE_URL"]).hostname == "api.z.ai" else ChatOpenAI
    return adapter(model=os.environ["LLM_MODEL"], api_key=os.environ["LLM_API_KEY"],
                      base_url=os.environ["LLM_BASE_URL"], temperature=0.2,
                      timeout=90, max_retries=1, max_tokens=int(os.getenv("LLM_MAX_TOKENS", "6000")),
                      default_headers={"X-Title": "GastroGuide"},
                      stream_usage=False, **options)
