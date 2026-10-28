"""Small JSON-model boundary: Nebius Token Factory (NVIDIA Nemotron).

    python -m engine.llm models    # list the models your NEBIUS_API_KEY can call
"""

from dataclasses import dataclass, field
import json
import logging
import os
import re
import socket
import time
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


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


NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
DEFAULT_EXTRACTION_MODEL = "nvidia/nemotron-3-super-120b-a12b"
DEFAULT_VISION_MODEL = "nvidia/Nemotron-3.5-Super-VL"
DEFAULT_JUDGE_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"
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
        for attempt in range(3):
            try:
                response = urlopen(request, timeout=timeout)
                break
            except HTTPError as error:
                if error.code not in {500, 502, 503, 504, 529} or attempt == 2:
                    raise
                status = error.code
                error.close()
                delay = 2 ** attempt
                logger.warning("Nebius returned HTTP %s; retrying in %s s (attempt %s/3)",
                               status, delay, attempt + 2)
                time.sleep(delay)
        with response:
            # The socket timeout bounds each read, not the whole answer: a server that keeps sending a byte now and
            # then held one call open for almost two hours. Read in chunks against a deadline instead.
            read1 = getattr(response, "read1", None)  # what has arrived, never waiting for more
            if read1 is None:
                body = response.read(5_000_001)
            else:
                deadline, chunks, size = time.monotonic() + timeout, [], 0
                while size <= 5_000_000:
                    if time.monotonic() > deadline:
                        raise TimeoutError
                    chunk = read1(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                body = b"".join(chunks)
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
        if 500 <= status < 600:
            raise ModelError(f"Nebius is temporarily unavailable (HTTP {status}); try again later.") from None
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
    # Optional transport injection; the ordinary CLI retains the existing transport.
    request_transport: Callable = field(default=_nebius_request, repr=False, compare=False)

    def complete_json(self, system_prompt: str, content: str | list[dict]) -> ModelReply:
        """One call, logged with its model, tokens and duration (never the prompt or the answer)."""
        started = time.monotonic()
        try:
            reply = self._complete_json(system_prompt, content)
        except ModelError as error:
            logger.warning("%s: call failed after %.1f s (%s: %s)", self.model_name, time.monotonic() - started,
                           type(error).__name__, str(error)[:300])
            raise
        u = reply.usage
        logger.info("%s: %s in / %s out tokens, %.1f s%s", self.model_name, f"{u['input_tokens']:,}" if "input_tokens" in u
                    else "?", f"{u['output_tokens']:,}" if "output_tokens" in u else "?", time.monotonic() - started,
                    f" · request {reply.request_id}" if reply.request_id else "")
        return reply

    def _complete_json(self, system_prompt: str, content: str | list[dict]) -> ModelReply:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        response_data = self.request_transport(self.api_key, self.base_url, "/chat/completions", payload, self.timeout_seconds)
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


class BudgetExceeded(ModelError):
    """The run's token or time budget would be exceeded by the next call; the rest waits for the next run."""


RUN_TOKENS = 2_000_000  # per run; REFRESHER_RUN_TOKENS overrides
RUN_SECONDS = 1800  # per run; REFRESHER_RUN_SECONDS overrides


class BudgetedModel:
    """A spending cap for one run, after the metered gate in Victor's news map: each call reserves its worst case
    (input estimated at one token per 3 bytes, plus the model's whole output allowance) and is refused before it is
    sent if that could pass the run's token budget, or if its timeout could pass the run's time budget. Afterwards
    the call is charged what the provider reports (or the estimate). Tokens, not money: a price per token is not
    verified for every model, so a currency cap would only look precise."""

    def __init__(self, model: JsonModel, max_tokens: int | None = None, max_seconds: float | None = None,
                 clock=time.monotonic):
        self.model, self.clock = model, clock
        self.provider = getattr(model, "provider", None)
        self.model_provider = getattr(model, "model_provider", None)
        self.model_name = getattr(model, "model_name", None)
        self.max_tokens = max_tokens if max_tokens is not None else int(os.environ.get("REFRESHER_RUN_TOKENS") or RUN_TOKENS)
        self.max_seconds = max_seconds if max_seconds is not None else \
            float(os.environ.get("REFRESHER_RUN_SECONDS") or RUN_SECONDS)
        self.started, self.spent, self.calls = clock(), 0, 0

    @staticmethod
    def estimate(text: str) -> int:
        return -(-len(text.encode("utf-8")) // 3)

    def complete_json(self, system_prompt: str, content: str) -> ModelReply:
        prompt = self.estimate(system_prompt) + self.estimate(content)
        reserve = prompt + getattr(self.model, "max_tokens", 16384)
        if self.spent + reserve > self.max_tokens:
            raise BudgetExceeded(f"Run budget reached: {self.spent:,} of {self.max_tokens:,} tokens used, and the next "
                                 f"call could use {reserve:,}; the rest waits for the next run.")
        elapsed = self.clock() - self.started
        if elapsed + getattr(self.model, "timeout_seconds", 120) > self.max_seconds:
            raise BudgetExceeded(f"Run time budget reached after {elapsed:.0f} of {self.max_seconds:.0f} seconds; "
                                 "the rest waits for the next run.")
        self.calls += 1
        try:
            reply = self.model.complete_json(system_prompt, content)
        except ModelError:
            self.spent += prompt  # a failed call still read the prompt
            raise
        used = reply.usage.get("total_tokens") or prompt + self.estimate(json.dumps(reply.data, ensure_ascii=False))
        self.spent += used
        return reply


def list_nebius_models(api_key: str, base_url: str = NEBIUS_BASE_URL) -> list[str]:
    data = _nebius_request(api_key, base_url, "/models", None, 30)
    return sorted(m["id"] for m in data.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str))


def _nebius_from_environment(model_variable: str, timeout_seconds: int) -> NebiusModel:
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not key:
        raise ModelError("Add NEBIUS_API_KEY to .env to use Nebius Token Factory.")
    default = DEFAULT_JUDGE_MODEL if model_variable == "NEBIUS_JUDGE_MODEL" else DEFAULT_EXTRACTION_MODEL
    if model_variable == "NEBIUS_VISION_MODEL":
        default = DEFAULT_VISION_MODEL
    model_name = os.environ.get(model_variable, "").strip() or default
    budget = os.environ.get("NEBIUS_MAX_TOKENS", "32768").strip()
    if model_variable == "NEBIUS_JUDGE_MODEL":
        budget = os.environ.get("NEBIUS_JUDGE_MAX_TOKENS", "").strip() or budget
    try:
        max_tokens = int(budget)
        if not 1 <= max_tokens <= 131072:
            raise ValueError
    except ValueError:
        raise ModelError("NEBIUS_MAX_TOKENS/NEBIUS_JUDGE_MAX_TOKENS must be integers from 1 to 131072.") from None
    base_url = os.environ.get("NEBIUS_BASE_URL", "").strip() or NEBIUS_BASE_URL
    return NebiusModel(key, model_name, base_url=base_url, timeout_seconds=timeout_seconds, max_tokens=max_tokens)


def client_from_environment() -> JsonModel | None:
    provider = os.environ.get("TRIAGE_PROVIDER", "off").strip().lower()
    if provider in {"", "off"}:
        return None
    if provider == "nebius":
        return _nebius_from_environment("NEBIUS_MODEL", 120)
    raise ModelError("TRIAGE_PROVIDER must be nebius or off.")


def judge_client_from_environment() -> JsonModel | None:
    """Separate opt-in model for source-faithfulness review."""
    provider = os.environ.get("JUDGE_PROVIDER", "off").strip().lower()
    if provider in {"", "off"}:
        return None
    if provider == "nebius":
        return _nebius_from_environment("NEBIUS_JUDGE_MODEL", 180)
    raise ModelError("JUDGE_PROVIDER must be nebius or off.")


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
