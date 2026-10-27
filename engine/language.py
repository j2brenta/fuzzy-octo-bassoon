"""The one language the pipeline writes in: claims, review reasons and summaries, triage reasons.

Sources are never translated for judging. Models read the original passages and write their output in
REFRESHER_LANGUAGE (an ISO code, default en), so a vault mixing Russian, German and French sources reads, links and
deduplicates in one language while every citation still points at the original text. Names keep their source
spelling. REFRESHER_LANGUAGE=source restores the earlier behaviour: output in each source's own language.
"""
import os

MARKER = "<<OUTPUT_LANGUAGE>>"
NAMES = {"en": "English", "ru": "Russian", "de": "German", "fr": "French", "uk": "Ukrainian", "be": "Belarusian",
         "es": "Spanish", "it": "Italian", "pl": "Polish", "pt": "Portuguese", "nl": "Dutch"}
DEFAULT = "en"


def code(env=None) -> str:
    """The configured output language: an ISO code, or "source"."""
    env = os.environ if env is None else env
    value = (env.get("REFRESHER_LANGUAGE") or DEFAULT).strip().lower()
    return value if value == "source" or value in NAMES else DEFAULT


def name(env=None) -> str:
    """How prompts say it: "English", or "the source language"."""
    value = code(env)
    return "the source language" if value == "source" else NAMES[value]


def apply(prompt: str, env=None) -> str:
    """The prompt with its output language filled in."""
    return prompt.replace(MARKER, name(env))
