import logging

from engine import logs
from engine.llm import ModelError, NebiusModel


def model(transport):
    return NebiusModel("fixture-key", "fixture/model", request_transport=transport)


def test_each_model_call_logs_model_tokens_and_time_but_not_the_prompt(caplog):
    def transport(*_):
        return {"id": "req-1", "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 34, "total_tokens": 1234}}
    with caplog.at_level(logging.INFO, logger="engine.llm"):
        assert model(transport).complete_json("secret system prompt", "secret content").data == {"ok": True}
    [line] = caplog.messages
    assert line.startswith("fixture/model: 1,200 in / 34 out tokens,") and "request req-1" in line
    assert "secret" not in line


def test_a_failed_model_call_is_logged_and_still_raised(caplog):
    def transport(*_):
        raise ModelError("Nebius HTTP 429: rate limited.")
    with caplog.at_level(logging.INFO, logger="engine.llm"):
        try:
            model(transport).complete_json("prompt", "content")
        except ModelError:
            pass
        else:
            raise AssertionError("expected ModelError")
    [record] = caplog.records
    assert record.levelno == logging.WARNING and "ModelError: Nebius HTTP 429" in record.getMessage()


def test_log_level_comes_from_the_environment_and_aiogram_updates_stay_quiet():
    root = logging.getLogger()
    before = root.level, list(root.handlers), logging.getLogger("aiogram.event").level
    try:
        logs.configure({"LOG_LEVEL": "warning"})
        assert root.level == logging.WARNING
        logs.configure({})
        assert root.level == logging.INFO and logging.getLogger("aiogram.event").level == logging.WARNING
        logs.configure({"LOG_LEVEL": "nonsense"})
        assert root.level == logging.INFO
    finally:
        root.setLevel(before[0])
        root.handlers[:] = before[1]
        logging.getLogger("aiogram.event").setLevel(before[2])
