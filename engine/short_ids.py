"""Short stand-ins (C1, C2 … / E1, E2 …) for 64-character ids in model requests.

Models reviewing many items drop characters from long hex ids, or lose track and
repeat some while skipping others. Requests carry short ids; replies are mapped
back before validation, so stored records and checks still use the real ids.
"""

from dataclasses import replace
import re


def short_ids(ids, prefix: str) -> tuple[dict, dict]:
    """Return (real -> short, short -> real) for ids in their given order."""
    to_short = {real: f"{prefix}{n}" for n, real in enumerate(ids, 1)}
    return to_short, {short: real for real, short in to_short.items()}


def restore(value, key: str, to_long: dict, text_keys=("reason", "summary")):
    """Copy of reply data with short ids under `key`, and inside free-text fields, replaced by real ids.

    Unknown short ids are left as they are, so validation still reports them.
    """
    prefixes = sorted({short.rstrip("0123456789") for short in to_long})
    pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, prefixes)) + r")\d+\b") if prefixes else None

    def walk(node, name=""):
        if isinstance(node, dict):
            return {k: walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, name) for v in node]
        if isinstance(node, str) and name == key:
            return to_long.get(node, node)
        if isinstance(node, str) and name in text_keys and pattern:
            return pattern.sub(lambda m: to_long.get(m.group(), m.group()), node)
        return node

    return walk(value)


def restore_reply(reply, key: str, to_long: dict):
    """ModelReply with its data restored; rejected answers are then saved with real ids too."""
    return replace(reply, data=restore(reply.data, key, to_long))
