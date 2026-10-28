"""Private text, picture and PDF capture bot. Run locally with: python -m channels.telegram."""

import argparse
import asyncio
from datetime import datetime, timezone
import getpass
import json
import logging
import os
from pathlib import Path
import sys
import time

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

from channels.pasted import telegram_copy_metadata
from store.raw import CaptureResult, capture_text
from channels.web import fetch_linked_articles
from channels.pdf import capture_pdf, interpret_pdf, pdf_attachment
from engine.parse.pdf import PdfError
from channels.pictures import capture_picture, interpret_picture, image_attachment
from engine.vision import ImageError
from store.media import MediaError
from engine.parse import parse_capture
from engine.parse.text import text_without_links
from engine import logs
from engine.llm import ModelError, client_from_environment, judge_client_from_environment
from engine.triage import TriageError, triage_capture
from engine.extract import ExtractionError, extract_capture
from engine.judge import JudgeError
from engine.quality import QualityError, quality_capture
from engine.entities import EntityError, entity_client_from_environment, extract_entities, review_entities
from engine.package import PackageError, build_extraction
from engine.placement import apply_storage_stub
from engine.results import write_item_note, entity_summary
from graph import embed
from graph.relate import ModelRelator
from refresher.consolidate import consolidate, due, pending, review_on
from refresher.place import place, reply_text

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
logger = logging.getLogger(__name__)


async def stage(label: str, name: str, fn, *args, describe=None, detail: bool = True, **kwargs):
    """Run one pipeline step in a thread and log its start, outcome and duration. `describe(result)` says what it
    decided; a failure logs its type, and its message unless `detail` is False (capture and parsing errors can
    quote the message), then propagates to the caller's handling."""
    logger.info("%s · %s: started", label, name)
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as error:
        reason = f"{type(error).__name__}: {str(error)[:300]}" if detail else type(error).__name__
        logger.warning("%s · %s: failed after %.1f s (%s)", label, name, time.monotonic() - started, reason)
        raise
    logger.info("%s · %s: %s (%.1f s)", label, name, describe(result) if describe else "done",
                time.monotonic() - started)
    return result


def item_label(item_id: str) -> str:
    return f"item {item_id[:10]}"


def cached(result) -> str:
    return " (cached)" if getattr(result, "reused", False) else ""


def user_hint(user: User) -> dict[str, str]:
    hint = {"id": str(user.id), "name": user.full_name}
    if user.username:
        hint["handle"] = user.username
    return hint


def capture_message(message: Message, vault_path: Path) -> CaptureResult:
    """Translate Telegram metadata while preserving original text separately."""
    if message.text is None:
        raise ValueError("This block supports text messages only.")
    return capture_text(message.text, vault_path, channel="telegram", **message_fields(message))


def message_fields(message: Message) -> dict:
    """Share provenance between text and image without inventing original authors."""
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
        forwarder = ({"name": message.from_user.full_name, "url": f"tg://user?id={message.from_user.id}",
                          **({"handle": message.from_user.username} if message.from_user.username else {})}
                         if message.from_user else None)
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
    copied = telegram_copy_metadata(message.text or "")
    if copied:
        metadata["telegram_copy"] = copied
        if origin is None:
            # The Telegram sender pasted this text; that is not proof they wrote it.
            author = None
            forwarder = ({"name": message.from_user.full_name, "url": f"tg://user?id={message.from_user.id}",
                          **({"handle": message.from_user.username} if message.from_user.username else {})}
                         if message.from_user else None)

    if origin is None and (image_attachment(message) is not None or pdf_attachment(message) is not None):
        # An uploader is known; the creator/author of a screenshot is not.
        author = None
        forwarder = ({"name": message.from_user.full_name, "url": f"tg://user?id={message.from_user.id}",
                      **({"handle": message.from_user.username} if message.from_user.username else {})}
                     if message.from_user else None)

    return dict(
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
            await message.answer("Refresher is ready. Send text, a link, a picture or a PDF (up to 20 MB / 20 pages).")
        else:
            await message.answer("This bot accepts notes only from its configured owner.")
        return
    if owner_id is None:
        await message.answer("Capture is disabled in setup mode. Send /start for your user ID.")
        return
    if message.from_user.id != owner_id:
        await message.answer("This bot accepts notes only from its configured owner.")
        return
    kind = ("forwarded " if message.forward_origin is not None else "") + (
        "PDF" if pdf_attachment(message) is not None else "picture" if image_attachment(message) is not None
        else "text" if message.text is not None else "unsupported message")
    logger.info("message %s · received: %s, %d characters", message.message_id, kind, len(message.text or ""))
    if pdf_attachment(message) is not None:
        await handle_pdf(message, vault_path)
        return
    if image_attachment(message) is not None:
        await handle_picture(message, vault_path)
        return
    if message.text is None:
        await message.answer("Not saved: send text, a link, a JPEG/PNG/WebP picture or a PDF. Voice and other attachments are not supported.")
        return
    if message.forward_origin is None and command == "/consolidate":
        await message.answer("Reviewing the whole graph: relations, topics, merges and people…")
        try:
            run = await stage("review", "consolidate (/consolidate)", consolidate_now, vault_path,
                              client_from_environment(), describe=lambda r: r.summary())
        except (ModelError, OSError, ValueError) as error:
            logger.error("Consolidation failed (%s).", type(error).__name__)
            await message.answer(f"Review failed ({type(error).__name__}); the graph is unchanged.")
            return
        await message.answer(review_text(run))
        return
    if message.forward_origin is None and command.startswith("/"):
        await message.answer("Only /start, /help and /consolidate are available. Topic refreshers are not implemented yet.")
        return
    try:
        result = await stage(f"message {message.message_id}", "capture", capture_message, message, vault_path,
                             detail=False, describe=lambda r: f"{'already captured' if r.duplicate else 'captured'} as {r.item_id[:10]}")
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
        prepared = await stage(item_label(result.item_id), "prepare", parse_capture, result.path, detail=False,
                               describe=lambda p: f"{p.section_count} sections, {p.link_count} links{cached(p)}")
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
        "Links identified; article reading is a separate step."
    )
    document = json.loads(prepared.path.read_text())
    rows = []
    setting = os.environ.get("WEB_FETCH", "on").strip().lower()
    if setting not in {"on", "off"}:
        await message.answer("Original saved; WEB_FETCH must be on or off.")
        return
    if document["links"] and setting == "on":
        await message.answer("Reading linked articles/PDFs; your original message remains saved.")
        try:
            rows = await stage(item_label(result.item_id), "linked articles", fetch_linked_articles, result.path,
                               detail=False, describe=lambda found: ", ".join(
                                   f"{r.get('kind', 'article')} {r['status']}" + (f" as {r['item_id'][:10]}" if r.get("item_id") else "")
                                   for r in found) or "none")
        except (OSError, ValueError):
            await message.answer("Original saved; article processing could not finish. Retry this message later.")
        for row in rows:
            if row["status"] == "captured":
                status = ("Cached PDF" if row.get("reused") else "PDF saved") if row.get("kind") == "pdf" else ("Cached article" if row.get("reused") else "Article saved")
                await message.answer(f"{status}. Item: {row['item_id']}")
            else:
                await message.answer(f"Link {row['status']}: {row['reason']} Original retained.")

    # A bare URL is a pointer, not source text from which to invent article claims.
    remaining = text_without_links(message.text or "", document["links"])
    if not document["links"] or any(character.isalnum() for character in remaining):
        if rows:
            await message.answer("Processing the message text separately from its linked articles.")
        await process_capture(message, result)
    elif not rows and document["links"]:
        logger.info("%s · text: only links, no claims extracted from the message itself", item_label(result.item_id))
        await message.answer("URL saved. Article reading is off or unfinished; no article claims were extracted.")
    for row in rows:
        if row["status"] == "captured":
            await message.answer(f"Processing {'PDF' if row.get('kind') == 'pdf' else 'article'} item: {row['item_id']}")
            article = CaptureResult(
                result.path.parent / f"{row['item_id']}.json",
                row["item_id"],
                row.get("reused", False),
            )
            await process_capture(message, article)


async def handle_pdf(message: Message, vault_path: Path) -> None:
    receipt = None
    try:
        receipt = await capture_pdf(message, vault_path, message_fields(message))
        await message.answer("PDF saved. Extracting pages; scanned pages use Nebius vision. Your original is retained.")
        result = await stage(f"message {message.message_id}", "PDF reading", interpret_pdf, receipt,
                             describe=lambda r: f"saved as {r.item_id[:10]}")
    except (PdfError, MediaError, ModelError) as error:
        await message.answer(str(error) + (" PDF retained; resend it to retry." if receipt else ""))
        return
    except (OSError, ValueError, KeyError):
        logger.error("PDF processing could not finish; any saved original is retained.")
        await message.answer("PDF processing could not finish. Any saved original is retained; resend it to retry.")
        return
    raw = json.loads(result.path.read_text())
    pages = raw['channel_metadata']['pdf_reading']['page_count']
    await message.answer(f"PDF prepared: {pages} page(s). Page references are saved with the extracted text.")
    await process_capture(message, result)


async def handle_picture(message: Message, vault_path: Path) -> None:
    receipt = None
    try:
        receipt = await capture_picture(message, vault_path, message_fields(message))
        await message.answer("Picture saved. Reading text and visual content with Nebius; your original is retained.")
        result = await stage(f"message {message.message_id}", "picture reading", interpret_picture, receipt,
                             describe=lambda r: f"saved as {r.item_id[:10]}")
    except (ImageError, MediaError, ModelError) as error:
        await message.answer(str(error) + (" Picture retained; resend it to retry." if receipt else ""))
        return
    except (OSError, ValueError, KeyError):
        logger.error("Picture processing could not finish; any saved source is retained.")
        await message.answer("Picture processing could not finish. Any saved original is retained; resend it to retry.")
        return
    raw = json.loads(result.path.read_text())
    text = raw['text']
    preview = text[:1800] + ("…\nFull text is in the saved result." if len(text)>1800 else "")
    await message.answer("Image reading (automatic; may contain errors):\n"+preview)
    await process_capture(message, result)


async def process_capture(message: Message, result: CaptureResult) -> None:
    """Always refresh a readable result, including when a model stage is pending."""
    try:
        await _process_capture(message, result)
    finally:
        try:
            await asyncio.to_thread(write_item_note, result.path)
        except (OSError, ValueError, KeyError):
            logger.warning("Readable result could not be refreshed for %s.", result.item_id)


async def _process_capture(message: Message, result: CaptureResult) -> None:
    """Run the same reviewed text pipeline for a message or a downloaded article."""
    label = item_label(result.item_id)
    try:
        client = client_from_environment()
        if client is None:
            logger.info("%s · AI processing is off (TRIAGE_PROVIDER); stopping after capture", label)
            return
        decision = await stage(label, "triage", triage_capture, result.path, client, describe=lambda d: (
            f"{'keep' if d.labels['keep'] else 'skip'} · {d.labels['kind']} · {d.labels['durability_class']}{cached(d)}"))
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
        extraction = await stage(label, "claim extraction", extract_capture, result.path, client,
                                 describe=lambda x: f"{len(x.claims)} claims{cached(x)}")
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
            logger.info("%s · quality review is off (JUDGE_PROVIDER); the item stays unreviewed", label)
            return
        quality = await stage(label, "quality review", quality_capture, result.path, judge_client, describe=lambda q: (
            f"{q.reviewed.review['status']} · "
            f"{sum(r['verdict'] == 'supported' for r in q.reviewed.review['claims'])}/{len(q.reviewed.review['claims'])} supported · "
            f"{len(q.reviewed.review['omitted_concepts'])} coverage notes"
            + "".join(f" · {flag}" for flag, on in (("corrected", q.corrected), ("expanded", q.expanded),
                                                     ("citations repaired", q.citations_repaired)) if on)))
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
        f"{len(review['omitted_concepts'])} coverage note(s).\n"
        f"{review['summary']}\n"
        + ("One automatic correction was applied and reviewed.\n" if quality.corrected else "")
        + ("One coverage pass added missing source-backed ideas and was reviewed.\n" if quality.expanded else "")
        + ("Source citations were repaired and reviewed.\n" if quality.citations_repaired else "")
        + "This checks faithfulness to your source, not whether the source is true."
    )

    try:
        entity_client = entity_client_from_environment()
        if entity_client is None:
            logger.info("%s · entity extraction is off (ENTITY_EXTRACTION); the handoff waits for it", label)
            return
        entity_result = await stage(label, "entities", extract_entities, result.path, entity_client,
                                    describe=lambda e: f"{len(e.entities)} entities{cached(e)}")
        entity_review = await stage(label, "entity review", review_entities, result.path, entity_client, entity_result,
                                    describe=lambda r: f"{r.review.get('status', 'reviewed')}{cached(r)}")
    except (ModelError, EntityError) as error:
        await message.answer(f"Claims saved; entity extraction or review is pending. {error}")
        return
    except (OSError, ValueError):
        await message.answer("Claims saved; entity processing could not be saved and remains pending.")
        return
    await message.answer(
        entity_summary(entity_result.entities, entity_review.review)
        + "\nClear aliases are grouped within this article; source identities remain separate."
    )

    try:
        packaged = await stage(label, "handoff", build_extraction, result.path,
                               claims_from=quality.extraction_path.parent.name, describe=lambda k: (
                                   f"{len(k.record['claims'])} claims, {len(k.record['entities'])} entities{cached(k)}"))
        await stage(label, "storage receipt", apply_storage_stub, packaged.path)  # validates the record, keeps a receipt
    except (PackageError, ExtractionError, EntityError, JudgeError, TriageError, OSError, ValueError):
        await message.answer("Claims and entities saved; the combined extraction handoff remains pending.")
        return
    saved = f"Combined extraction saved: {len(packaged.record['claims'])} claims and {len(packaged.record['entities'])} entities."
    try:
        placed, store, note = await stage(
            label, "graph placement", place, packaged.path.parent.parent, packaged.path.stem, graph_relator(client),
            describe=lambda r: f"{r[0].status} · {r[0].claims} claims · {len(r[0].relations)} relations · "
                               f"{len(set(r[0].ideas))} Ideas" + (f" · repost of {r[0].duplicate_of[:10]}" if r[0].duplicate_of else "")
                               + (f" · {r[2]}" if r[2] else ""))
    except (OSError, ValueError) as error:
        logger.error("Graph placement failed (%s).", type(error).__name__)
        await message.answer(f"{saved}\nGraph placement failed; the next rebuild places it.")
        return
    waiting = sum(s.status == "proposed" for s in store.proposals.values())
    tail = f"\n{waiting} proposal(s) wait for you: run `refresher serve` to review them." if waiting else ""
    await message.answer(f"{saved}\n{reply_text(placed, store, note)}{tail}")
    vault_path = packaged.path.parent.parent
    if review_on() and due(vault_path):
        # The hourly review rides on captures, after the reply: the user is not kept waiting, and the bot
        # handles one message at a time, so the next capture simply starts when it is done.
        try:
            await stage("review", "hourly consolidate", consolidate_now, vault_path, client, if_due=True,
                        describe=described)
        except (ModelError, OSError, ValueError) as error:
            logger.error("Hourly consolidation failed (%s).", type(error).__name__)


def consolidate_now(vault_path: Path, client, if_due: bool = False):
    """The hourly job with the bot's model (none when GRAPH_RELATOR=lexical) and Tavily when its key is set.
    With `if_due`, None when another runner (the review container, a scheduled job) has just done it."""
    from engine.people import search_from_environment
    model = client if client is not None and graph_relator(client) is not None else None
    return consolidate(vault_path, model, search_from_environment() if model is not None else None, if_due=if_due)


def described(run) -> str:
    return run.summary() if run is not None else "skipped: another runner has just reviewed"


def review_text(run) -> str:
    lines = [f"Reviewed: {run.duplicates} duplicate items, {run.firmed} Ideas firmed, {run.proposed} new proposals."]
    if run.enriched:
        lines.append(f"Looked up {run.enriched} people.")
    lines.append(f"{run.waiting} proposal(s) wait for you: run `refresher serve` to review them."
                 if run.waiting else "Nothing waits for you.")
    lines += [n[0].upper() + n[1:] + "." for n in run.notes or []]
    return "\n".join(lines)


def graph_relator(client):
    """The model judges relations unless GRAPH_RELATOR=lexical; place() falls back to wording if the model fails."""
    return None if os.environ.get("GRAPH_RELATOR", "model").strip().lower() == "lexical" else ModelRelator(client)


REVIEW_CHECK_SECONDS = 600  # how often the bot looks whether the hourly review is due


async def review_on_a_timer(vault_path: Path, every: float = REVIEW_CHECK_SECONDS) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            if await asyncio.to_thread(pending, vault_path):
                await stage("review", "timed consolidate", consolidate_now, vault_path, client_from_environment(),
                            if_due=True, describe=described)
        except Exception:  # stage() logged it; the timer keeps going
            continue


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
        timer = asyncio.create_task(review_on_a_timer(vault_path)) if owner_id is not None and review_on() else None
        try:
            # Process each delivery before accepting the next polling offset.
            await dispatcher.start_polling(bot, allowed_updates=["message"], handle_as_tasks=False)
        finally:
            if timer is not None:
                timer.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=Path(".local-vault/telegram"))
    parser.add_argument("--owner-id", type=int, help="Your numeric Telegram user ID; omitted means setup mode")
    args = parser.parse_args()
    # Only read this project's .env; explicitly exported variables take precedence.
    load_dotenv(ENV_FILE, override=False)
    logs.configure(vault=args.vault, process="bot")
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
    # Placement and the hourly review embed with Nebius when NEBIUS_EMBED_MODEL is set, as the CLI does.
    embed.use(embed.from_environment(args.vault))
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
