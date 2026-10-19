"""Small JSON-model boundary; Backboard is the temporary development provider."""

from dataclasses import dataclass, field
import json
import os
import socket
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelError(RuntimeError):
    """A safe, user-facing error that never embeds headers or response bodies."""


class ModelOutputError(ModelError):
    """The response arrived, but the model did not return the required JSON."""


@dataclass(frozen=True)
class ModelReply:
    data: dict
    actual_provider: str | None = None
    actual_model: str | None = None
    request_id: str | None = None
    usage: dict = field(default_factory=dict)


class JsonModel(Protocol):
    provider: str
    model_provider: str
    model_name: str

    def complete_json(self, system_prompt: str, content: str) -> ModelReply: ...


@dataclass(frozen=True)
class BackboardModel:
    api_key: str = field(repr=False)
    model_provider: str = "openai"
    model_name: str = "gpt-4o-mini"
    provider: str = field(default="backboard", init=False)

    def complete_json(self, system_prompt: str, content: str) -> ModelReply:
        payload = {
            "content": content,
            "system_prompt": system_prompt,
            "llm_provider": self.model_provider,
            "model_name": self.model_name,
            "stream": False,
            "json_output": True,
            "memory": "off",
            "web_search": "off",
        }
        # A fresh conversation per call avoids unrelated note history influencing
        # classification. Memory off does not mean Backboard deletes that history.
        request = Request(
            "https://app.backboard.io/api/threads/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ModelError("Backboard response exceeded the expected size.")
            response_data = json.loads(body)
        except HTTPError as error:
            status = error.code
            error.close()
            if status in {401, 403}:
                raise ModelError("Backboard rejected the API key or model access.") from None
            if status == 429:
                raise ModelError("Backboard rate limit or credit limit reached; try later.") from None
            raise ModelError(f"Backboard returned HTTP {status}; check provider/model settings.") from None
        except (URLError, TimeoutError, socket.timeout):
            raise ModelError("Could not reach Backboard within the request timeout.") from None
        except (ValueError, UnicodeError):
            raise ModelError("Backboard returned an unreadable response.") from None
        if not isinstance(response_data, dict):
            raise ModelError("Backboard returned an unexpected response format.")
        if response_data.get("status") not in {None, "COMPLETED"}:
            raise ModelError("Backboard did not complete the model response.")
        content = response_data.get("content")
        if not isinstance(content, str):
            raise ModelError("Backboard response did not contain model text.")
        try:
            data = json.loads(content)
        except ValueError:
            raise ModelOutputError("The model did not return valid JSON.") from None
        if not isinstance(data, dict):
            raise ModelOutputError("The model response must be a JSON object.")
        usage = {}
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = response_data.get(name)
            if type(value) is int and value >= 0:
                usage[name] = value
        def optional_text(name):
            value = response_data.get(name)
            return value if isinstance(value, str) else None
        return ModelReply(
            data=data,
            actual_provider=optional_text("model_provider"),
            actual_model=optional_text("model_name"),
            request_id=optional_text("message_id"),
            usage=usage,
        )


def client_from_environment() -> JsonModel | None:
    provider = os.environ.get("TRIAGE_PROVIDER", "off").strip().lower()
    if provider in {"", "off"}:
        return None
    if provider != "backboard":
        raise ModelError("TRIAGE_PROVIDER must be backboard or off; Nebius integration is pending.")
    key = os.environ.get("BACKBOARD_API_KEY", "").strip()
    if not key:
        raise ModelError("Add BACKBOARD_API_KEY to .env to enable AI triage.")
    model_provider = os.environ.get("BACKBOARD_LLM_PROVIDER", "openai").strip()
    model_name = os.environ.get("BACKBOARD_MODEL", "gpt-4o-mini").strip()
    if not model_provider or not model_name:
        raise ModelError("BACKBOARD_LLM_PROVIDER and BACKBOARD_MODEL must not be blank.")
    return BackboardModel(key, model_provider, model_name)
