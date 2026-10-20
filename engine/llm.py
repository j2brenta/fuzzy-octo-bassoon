"""Small JSON-model boundary: Nebius Token Factory (NVIDIA Nemotron), or Backboard for development.

    python -m engine.llm models    # list the models your NEBIUS_API_KEY can call
"""

from dataclasses import dataclass, field
import json
import os
import re
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
    timeout_seconds: int = 45

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
            with urlopen(request, timeout=self.timeout_seconds) as response:
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


NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
# Reasoning models may prefix the answer with a thinking block or wrap it in a code fence.
_THINKING = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def _nebius_request(api_key: str, base_url: str, path: str, payload: dict | None, timeout: int) -> dict:
    request = Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise ModelError("Nebius response exceeded the expected size.")
        data = json.loads(body)
    except HTTPError as error:
        status = error.code
        error.close()
        if status in {401, 403}:
            raise ModelError("Nebius rejected the API key or model access.") from None
        if status == 404:
            raise ModelError("Nebius does not know this model; run python -m engine.llm models.") from None
        if status == 429:
            raise ModelError("Nebius rate limit or credit limit reached; try later.") from None
        raise ModelError(f"Nebius returned HTTP {status}; check NEBIUS_BASE_URL and the model name.") from None
    except (URLError, TimeoutError, socket.timeout):
        raise ModelError("Could not reach Nebius within the request timeout.") from None
    except (ValueError, UnicodeError):
        raise ModelError("Nebius returned an unreadable response.") from None
    if not isinstance(data, dict):
        raise ModelError("Nebius returned an unexpected response format.")
    return data


@dataclass(frozen=True)
class NebiusModel:
    """OpenAI-compatible chat completions on Nebius Token Factory, in JSON mode."""

    api_key: str = field(repr=False)
    model_name: str
    model_provider: str = "nebius"
    provider: str = field(default="nebius", init=False)
    base_url: str = NEBIUS_BASE_URL
    timeout_seconds: int = 120
    # Reasoning models spend part of the budget thinking; a small cap leaves the answer empty.
    max_tokens: int = 16384

    def complete_json(self, system_prompt: str, content: str) -> ModelReply:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        response_data = _nebius_request(self.api_key, self.base_url, "/chat/completions", payload, self.timeout_seconds)
        try:
            choice = response_data["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelError("Nebius response did not contain model text.") from None
        if not isinstance(text, str) or not text.strip():
            # Usually the reasoning used the whole budget; a retry often succeeds.
            reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            raise ModelOutputError(f"Nebius returned no answer text (finish_reason={reason}).")
        if isinstance(choice, dict) and choice.get("finish_reason") == "length":
            raise ModelOutputError("Nebius answer was cut off at max_tokens.")
        text = _THINKING.sub("", text).strip()
        fenced = _FENCE.match(text)
        try:
            data = json.loads(fenced.group(1) if fenced else text)
        except ValueError:
            raise ModelOutputError("The model did not return valid JSON.") from None
        if not isinstance(data, dict):
            raise ModelOutputError("The model response must be a JSON object.")
        raw_usage = response_data.get("usage") if isinstance(response_data.get("usage"), dict) else {}
        usage = {}
        for ours, theirs in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
            value = raw_usage.get(theirs)
            if type(value) is int and value >= 0:
                usage[ours] = value
        model = response_data.get("model")
        request_id = response_data.get("id")
        return ModelReply(
            data=data,
            actual_provider="nebius",
            actual_model=model if isinstance(model, str) else None,
            request_id=request_id if isinstance(request_id, str) else None,
            usage=usage,
        )


def list_nebius_models(api_key: str, base_url: str = NEBIUS_BASE_URL) -> list[str]:
    data = _nebius_request(api_key, base_url, "/models", None, 30)
    return sorted(m["id"] for m in data.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str))


def _nebius_from_environment(model_variable: str, timeout_seconds: int) -> NebiusModel:
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not key:
        raise ModelError("Add NEBIUS_API_KEY to .env to use Nebius Token Factory.")
    model_name = os.environ.get(model_variable, "").strip()
    if not model_name:
        raise ModelError(f"Set {model_variable} in .env; python -m engine.llm models lists the choices.")
    base_url = os.environ.get("NEBIUS_BASE_URL", "").strip() or NEBIUS_BASE_URL
    return NebiusModel(key, model_name, base_url=base_url, timeout_seconds=timeout_seconds)


def client_from_environment() -> JsonModel | None:
    provider = os.environ.get("TRIAGE_PROVIDER", "off").strip().lower()
    if provider in {"", "off"}:
        return None
    if provider == "nebius":
        return _nebius_from_environment("NEBIUS_MODEL", 120)
    if provider != "backboard":
        raise ModelError("TRIAGE_PROVIDER must be nebius, backboard or off.")
    key = os.environ.get("BACKBOARD_API_KEY", "").strip()
    if not key:
        raise ModelError("Add BACKBOARD_API_KEY to .env to enable AI triage.")
    model_provider = os.environ.get("BACKBOARD_LLM_PROVIDER", "openai").strip()
    model_name = os.environ.get("BACKBOARD_MODEL", "gpt-4o-mini").strip()
    if not model_provider or not model_name:
        raise ModelError("BACKBOARD_LLM_PROVIDER and BACKBOARD_MODEL must not be blank.")
    return BackboardModel(key, model_provider, model_name)


def judge_client_from_environment() -> JsonModel | None:
    """Separate opt-in model for source-faithfulness review."""
    provider = os.environ.get("JUDGE_PROVIDER", "off").strip().lower()
    if provider in {"", "off"}:
        return None
    if provider == "nebius":
        return _nebius_from_environment("NEBIUS_JUDGE_MODEL", 180)
    if provider != "backboard":
        raise ModelError("JUDGE_PROVIDER must be nebius, backboard or off.")
    key = os.environ.get("BACKBOARD_API_KEY", "").strip()
    model_provider = os.environ.get("BACKBOARD_JUDGE_LLM_PROVIDER", "anthropic").strip()
    model_name = os.environ.get("BACKBOARD_JUDGE_MODEL", "claude-opus-5-5").strip()
    if not key or not model_provider or not model_name:
        raise ModelError("Configure BACKBOARD_API_KEY and nonblank judge model settings.")
    return BackboardModel(key, model_provider, model_name, timeout_seconds=180)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    if sys.argv[1:] != ["models"]:
        sys.exit(__doc__)
    try:
        key = os.environ.get("NEBIUS_API_KEY", "").strip()
        if not key:
            raise ModelError("Add NEBIUS_API_KEY to .env first.")
        for name in list_nebius_models(key, os.environ.get("NEBIUS_BASE_URL", "").strip() or NEBIUS_BASE_URL):
            print(name)
    except ModelError as error:
        sys.exit(str(error))
