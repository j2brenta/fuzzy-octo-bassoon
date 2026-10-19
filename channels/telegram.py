"""Private text-capture bot. Run locally with: python -m channels.telegram."""

import argparse
import asyncio
from datetime import datetime, timezone
import getpass
import logging
import os
from pathlib import Path
import sys

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramConflictError, TelegramUnauthorizedError
from aiogram.types import (
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
    User,
)
from aiogram.utils.token import TokenValidationError
from aiogram.utils.serialization import deserialize_telegram_object_to_python
from dotenv import load_dotenv

from store.raw import CaptureResult, capture_text
from engine.parse import parse_capture
from engine.llm import ModelError, client_from_environment, judge_client_from_environment
from engine.triage import TriageError, triage_capture
from engine.extract import ExtractionError, extract_capture
from engine.judge import JudgeError
from engine.quality import QualityError, quality_capture

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
logger = logging.getLogger(__name__)


def user_hint(user: User) -> dict[str, str]:
    hint = {"id": str(user.id), "name": user.full_name}
    if user.username:
        hint["handle"] = user.username
    return hint


def capture_message(message: Message, vault_path: Path) -> CaptureResult:
    """Translate Telegram metadata while preserving original text separately."""
    if message.text is None:
        raise ValueError("This block supports text messages only.")
    origin = message.forward_origin
    source_id = f"telegram:chat:{message.chat.id}"
    source_name = message.chat.title or message.chat.full_name or "Private Telegram chat"
    message_id = str(message.message_id)
    published_at = message.date
    author = user_hint(message.from_user) if message.from_user else None
    forwarder = None

    if origin is not None:
        published_at = origin.date
        author = None
        forwarder = str(message.from_user.id) if message.from_user else None
        # Only channel forwards expose an original message ID. Other forwarded
        # messages retain a receipt-scoped ID instead of guessing an original ID.
        delivery_id = f"delivery:{message.chat.id}:{message.message_id}"
        if isinstance(origin, MessageOriginChannel):
            source_id = f"telegram:channel:{origin.chat.id}"
            source_name = origin.chat.title or "Telegram channel"
            message_id = str(origin.message_id)
            if origin.author_signature:
                author = {"name": origin.author_signature}
        elif isinstance(origin, MessageOriginUser):
            source_id = f"telegram:user:{origin.sender_user.id}"
            source_name = origin.sender_user.full_name
            message_id = delivery_id
            author = user_hint(origin.sender_user)
        elif isinstance(origin, MessageOriginHiddenUser):
            source_name = "Forward with hidden sender"
            message_id = delivery_id
            author = {"name": origin.sender_user_name}
        elif isinstance(origin, MessageOriginChat):
            source_id = f"telegram:chat:{origin.sender_chat.id}"
            source_name = origin.sender_chat.title or "Telegram chat"
            message_id = delivery_id
            if origin.author_signature:
                author = {"name": origin.author_signature}

    # Keep the content once in the shared top-level text field. Telegram-specific
    # fields remain available for provenance and formatting.
    metadata = deserialize_telegram_object_to_python(message)
    metadata.pop("text", None)

    return capture_text(
        message.text,
        vault_path,
        channel="telegram",
        source_id=source_id,
        source_name=source_name,
        message_id=message_id,
        captured_at=datetime.now(timezone.utc).isoformat(),
        published_at=published_at.isoformat(),
        author_hint=author,
        forwarded_by=forwarder,
        channel_metadata=metadata,
    )


async def handle_message(message: Message, vault_path: Path, owner_id: int | None) -> None:
    """Acknowledge only after storage succeeds; no graph/extraction verdict yet."""
    if message.chat.type != "private" or message.from_user is None or message.from_user.is_bot:
        return
    text = message.text or ""
    first_word = text.split(maxsplit=1)
    command = first_word[0].split("@")[0] if first_word else ""
    if message.forward_origin is None and command in {"/start", "/help"}:
        if owner_id is None:
            await message.answer(
                f"Your Telegram user ID is {message.from_user.id}.\n"
                "Capture is disabled in setup mode. Stop the bot and restart "
                "with --owner-id followed by your ID."
            )
        elif message.from_user.id == owner_id:
            await message.answer("Refresher is ready. Send or forward a text message to save it.")
        else:
            await message.answer("This bot accepts notes only from its configured owner.")
        return
    if owner_id is None:
        await message.answer("Capture is disabled in setup mode. Send /start for your user ID.")
        return
    if message.from_user.id != owner_id:
        await message.answer("This bot accepts notes only from its configured owner.")
        return
    if message.text is None:
        await message.answer("Not saved: this version supports text only. Please paste the text; files and voice come later.")
        return
    if message.forward_origin is None and command.startswith("/"):
        await message.answer("Only /start and /help are available. Topic refreshers are not implemented yet.")
        return
    try:
        result = await asyncio.to_thread(capture_message, message, vault_path)
    except (OSError, ValueError) as error:
        # Log the category only: exception details can contain message content.
        category = type(error).__name__
        logger.error("Telegram capture failed (%s).", category)
        await message.answer(
            "Could not capture this message. Keep the original and try again. "
            f"Error category: {category}."
        )
        return
    status = "Already captured" if result.duplicate else "Captured"
    await message.answer(f"{status}.\nItem: {result.item_id}\nText and source metadata saved; AI processing comes later.")
    try:
        prepared = await asyncio.to_thread(parse_capture, result.path)
    except (OSError, ValueError) as error:
        category = type(error).__name__
        logger.error("Telegram text preparation failed (%s).", category)
        await message.answer(
            "Your original is saved, but text preparation failed. "
            f"Error category: {category}. You can retry preparation later."
        )
        return
    status = "Already prepared" if prepared.reused else "Prepared"
    await message.answer(
        f"{status}: {prepared.section_count} text section(s), {prepared.link_count} link(s). "
        "Links were identified, not downloaded."
    )
    try:
        client = client_from_environment()
        if client is None:
            return
        decision = await asyncio.to_thread(triage_capture, result.path, client)
    except (ModelError, TriageError) as error:
        await message.answer(f"Original saved and prepared; AI triage is pending. {error}")
        return
    except (OSError, ValueError) as error:
        logger.error("Telegram triage storage failed (%s).", type(error).__name__)
        await message.answer("Original saved and prepared; could not save the AI triage result. Please retry later.")
        return
    labels = decision.labels
    action = "keep" if labels["keep"] else "skip"
    status = "Cached triage" if decision.reused else "AI triage"
    await message.answer(
        f"{status}: {action} | {labels['kind']} | {labels['durability_class']}\n"
        f"{labels['reason']}\n"
        "This is a model judgment; the original is retained."
    )
    if not labels["keep"]:
        await message.answer("Claim extraction skipped by triage. Your original remains saved.")
        return
    try:
        extraction = await asyncio.to_thread(extract_capture, result.path, client)
    except (ModelError, ExtractionError) as error:
        await message.answer(f"Your original and triage are saved; claim extraction is pending. {error}")
        return
    except (OSError, ValueError) as error:
        logger.error("Telegram claim extraction storage failed (%s).", type(error).__name__)
        await message.answer("Your original and triage are saved; could not save extracted claims. Please retry later.")
        return
    status = "Cached extraction" if extraction.reused else "Extracted"
    lines = [f"{status}: {len(extraction.claims)} claim(s) with source references."]
    for claim in extraction.claims[:3]:
        statement = claim["statement"]
        if len(statement) > 200:
            statement = statement[:197] + "..."
        lines.append(f"- [{claim['kind']} | {claim['durability_class']}] {statement}")
    if len(extraction.claims) > 3:
        lines.append("More claims are saved in the local extraction record.")
    lines.append("References point to original passages; claims are not independently fact-checked.")
    await message.answer("\n".join(lines))
    try:
        judge_client = judge_client_from_environment()
        if judge_client is None:
            return
        quality = await asyncio.to_thread(quality_capture, result.path, judge_client)
        reviewed = quality.reviewed
    except (ModelError, JudgeError, ExtractionError, QualityError) as error:
        await message.answer(f"Claims saved; automated quality review is pending. {error}")
        return
    except (OSError, ValueError):
        await message.answer("Claims saved; automated quality review could not be saved. Review remains pending.")
        return
    review = reviewed.review
    passed = sum(row["verdict"] == "supported" for row in review["claims"])
    label = "accepted" if review["status"] == "accepted" else "needs revision"
    await message.answer(
        f"AI quality review: {label} | {passed}/{len(review['claims'])} claims supported | "
        f"{len(review['omitted_concepts'])} major omission(s).\n"
        f"{review['summary']}\n"
        + ("One automatic correction was applied and reviewed.\n" if quality.corrected else "")
        + ("One coverage pass added missing source-backed ideas and was reviewed.\n" if quality.expanded else "")
        + ("Source citations were repaired and reviewed.\n" if quality.citations_repaired else "")
        + "This checks faithfulness to your source, not whether the source is true."
    )


async def run_bot(token: str, vault_path: Path, owner_id: int | None) -> None:
    async with Bot(token=token) as bot:
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise ValueError("This bot already has a webhook. Use a separate development bot or stop that deployment before polling.")
        dispatcher = Dispatcher()

        @dispatcher.message()
        async def on_message(message: Message) -> None:
            await handle_message(message, vault_path, owner_id)

        mode = "capture enabled" if owner_id is not None else "setup mode: send /start to the bot"
        print(f"Telegram bot running ({mode}). Press Ctrl+C to stop.", flush=True)
        # Process each delivery before accepting the next polling offset.
        await dispatcher.start_polling(bot, allowed_updates=["message"], handle_as_tasks=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=Path(".local-vault/telegram"))
    parser.add_argument("--owner-id", type=int, help="Your numeric Telegram user ID; omitted means setup mode")
    args = parser.parse_args()
    # Only read this project's .env; explicitly exported variables take precedence.
    load_dotenv(ENV_FILE, override=False)
    owner_id = args.owner_id
    if owner_id is None:
        configured_owner = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
        if configured_owner:
            try:
                owner_id = int(configured_owner)
            except ValueError:
                parser.error("TELEGRAM_OWNER_ID must be a positive integer.")
    if owner_id is not None and owner_id <= 0:
        parser.error("Owner ID must be a positive integer.")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        if not sys.stdin.isatty():
            parser.exit(1, "Set TELEGRAM_BOT_TOKEN or start from an interactive terminal.\n")
        token = getpass.getpass("Paste your bot token (hidden; not saved): ").strip()
    if not token:
        parser.exit(1, "A bot token is required.\n")
    try:
        asyncio.run(run_bot(token, args.vault, owner_id))
    except (TokenValidationError, TelegramUnauthorizedError):
        parser.exit(1, "Bot token is invalid or was rejected by Telegram. Check it locally with BotFather.\n")
    except TelegramConflictError:
        parser.exit(1, "Telegram reports another active receiver. Stop the other bot process before retrying.\n")
    except ValueError as error:
        parser.exit(1, f"Cannot start bot: {error}\n")
    except KeyboardInterrupt:
        print("Bot stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
