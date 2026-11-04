"""Model-provider adapter. The product depends on this interface, not on one vendor.

``complete(messages, tools)`` takes OpenAI-style chat messages plus strict tool
schemas and returns the assistant message dict (``content`` and/or ``tool_calls``).
Any transport, quota or malformed-response problem becomes ``ProviderError`` with
a message that never includes the API key or the request body.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class ProviderError(Exception):
    pass


class OpenAICompatibleProvider:
    def __init__(self, api_key, model, base_url, timeout):
        self.api_key, self.model, self.base_url, self.timeout = api_key, model, base_url, timeout

    def complete(self, messages, tools):
        body = {"model": self.model, "messages": messages, "temperature": 0.1, "max_tokens": 700}
        if tools:
            body.update(tools=tools, tool_choice="auto")
        request = Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), method="POST",
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
            message = payload["choices"][0]["message"]
        except HTTPError as exc:
            raise ProviderError(f"The model provider returned HTTP {exc.code}.") from None
        except (URLError, TimeoutError, OSError):
            raise ProviderError("The model provider could not be reached.") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("The model provider sent an unreadable response.") from None
        if not isinstance(message, dict):
            raise ProviderError("The model provider sent an unreadable response.")
        return message


def get_provider():
    """The configured provider, or ``None`` when free-text answers are not switched on."""
    if not settings.ASSISTANT_AI_API_KEY:
        return None
    return OpenAICompatibleProvider(settings.ASSISTANT_AI_API_KEY, settings.ASSISTANT_AI_MODEL,
                                    settings.ASSISTANT_AI_BASE_URL, settings.ASSISTANT_AI_TIMEOUT)
