from engine.llm import ModelReply
from engine.short_ids import restore, restore_reply, short_ids

A, B = "a" * 64, "b" * 64


def test_round_trip_and_free_text():
    to_short, to_long = short_ids([A, B], "C")
    assert to_short == {A: "C1", B: "C2"}
    data = {"claims": [{"claim_id": "C1", "reason": "Duplicate of C2; see C12."}, {"claim_id": "C9"}], "summary": "C1 ok"}
    out = restore(data, "claim_id", to_long)
    assert out["claims"][0] == {"claim_id": A, "reason": f"Duplicate of {B}; see C12."}
    assert out["claims"][1]["claim_id"] == "C9"  # unknown stays, so validation reports it
    assert out["summary"] == f"{A} ok" and data["claims"][0]["claim_id"] == "C1"  # input untouched


def test_restore_reply_keeps_metadata():
    _, to_long = short_ids([A], "E")
    reply = restore_reply(ModelReply({"entities": [{"entity_id": "E1"}]}, actual_model="m"), "entity_id", to_long)
    assert reply.data == {"entities": [{"entity_id": A}]} and reply.actual_model == "m"
