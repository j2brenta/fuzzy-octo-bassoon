"""Bounded page-by-page PDF extraction with cached vision fallback."""

import hashlib
import json
import math
from pathlib import Path
from threading import RLock

import pypdfium2 as pdfium
from PIL import ImageStat

from engine.artifacts import atomic_json
from engine.vision import ImageError, read_image

PDF_VERSION = "pdf-read-v1"
MAX_PDF_BYTES = 20_000_000
MAX_PAGES = 20
MAX_TEXT_CHARACTERS = 50_000
_PDF_LOCK = RLock()  # PDFium calls must not run concurrently across threads.


class PdfError(ValueError):
    """Safe PDF failure; original files are retained."""


def cut_notice(read: int, total: int, *, whole: bool = True) -> str:
    """What a reader is told when a long PDF was read only up to the limits."""
    part = f"pages 1–{read} of {total} were" if whole else f"the start of page 1 of {total} was"
    return (f"This PDF is longer than Refresher reads ({MAX_PAGES} pages, {MAX_TEXT_CHARACTERS:,} characters), "
            f"so only {part} analyzed. The full original is saved.")


def pdf_signature(content: bytes) -> bool:
    return b"%PDF-" in content[:1024]


def read_pdf(path: Path, cache: Path, *, reader=None) -> dict:
    path, cache = Path(path), Path(cache)
    content = path.read_bytes()
    if len(content) > MAX_PDF_BYTES or not pdf_signature(content):
        raise PdfError("Send a valid PDF under 20 MB; original retained.")
    digest = hashlib.sha256(content).hexdigest()
    cache = cache / digest
    cache.mkdir(parents=True, exist_ok=True)
    pages = []
    text = ""
    with _PDF_LOCK:
        try:
            document = pdfium.PdfDocument(content)
        except pdfium.PdfiumError:
            raise PdfError(
                "PDF is damaged or password-protected. Send an unlocked, readable copy; original retained."
            ) from None
        try:
            count = len(document)
            if count < 1:
                raise PdfError("PDF has no pages; original retained.")
            whole = True
            for index in range(min(count, MAX_PAGES)):
                number = index + 1
                cached = cache / f"page-{number}.json"
                result = None
                if cached.exists():
                    try:
                        value = json.loads(cached.read_text())
                        cached_text = value.get("text")
                        if (
                            value.get("pdf_sha256") == digest
                            and value.get("page") == number
                            and value.get("parser_version") == PDF_VERSION
                            and value.get("method") in {"native", "vision", "blank"}
                            and isinstance(cached_text, str)
                            and value.get("text_sha256")
                            == hashlib.sha256(cached_text.encode()).hexdigest()
                        ):
                            result = value
                    except (OSError, ValueError, TypeError, AttributeError):
                        pass  # A damaged derived cache is rebuilt from the preserved PDF.
                if result is None:
                    page = document[index]
                    try:
                        textpage = page.get_textpage()
                        try:
                            # A page over the whole limit cannot fit: the reading ends before it, or
                            # starts with as much of it as fits.
                            over = textpage.count_chars() > MAX_TEXT_CHARACTERS
                            if over and pages:
                                break
                            native = (
                                textpage.get_text_range(count=MAX_TEXT_CHARACTERS if over else -1)
                                .replace("\r\n", "\n").strip()
                            )
                        finally:
                            textpage.close()
                        result = {
                            "parser_version": PDF_VERSION,
                            "pdf_sha256": digest,
                            "page": number,
                            "method": "native",
                            "text": native,
                        }
                        if sum(char.isalnum() for char in native) < 8:
                            width, height = page.get_size()
                            if not all(
                                math.isfinite(v) and v > 0 for v in (width, height)
                            ):
                                raise PdfError(
                                    f"Page {number} has invalid dimensions; original retained."
                                )
                            bitmap = page.render(
                                scale=min(2, 2048 / max(width, height))
                            )
                            try:
                                image = bitmap.to_pil().convert("RGB").copy()
                            finally:
                                bitmap.close()
                            if max(ImageStat.Stat(image).stddev) < 0.5:
                                result.update(method="blank", text="")
                            else:
                                rendered = cache / f"page-{number}.png"
                                image.save(rendered, format="PNG")
                                try:
                                    reading = (reader or read_image)(rendered)
                                except ImageError as error:
                                    raise PdfError(
                                        f"PDF page {number} could not be read: {error}"
                                    ) from None
                                parts = []
                                if reading["text"].strip():
                                    parts.append(reading["text"].strip())
                                if reading["description"].strip():
                                    parts.append(
                                        "Visual description (model-generated):\n"
                                        + reading["description"].strip()
                                    )
                                result.update(
                                    method="vision",
                                    text="\n\n".join(parts),
                                    vision={
                                        k: v
                                        for k, v in reading.items()
                                        if k not in {"text", "description"}
                                    },
                                    rendered_sha256=hashlib.sha256(
                                        rendered.read_bytes()
                                    ).hexdigest(),
                                )
                    finally:
                        page.close()
                    result["text_sha256"] = hashlib.sha256(
                        result["text"].encode()
                    ).hexdigest()
                    atomic_json(cached, result)
                page_text = f"PDF page {number}:\n" + result["text"]
                start = len(text) + (2 if text else 0)
                if start + len(page_text) > MAX_TEXT_CHARACTERS:
                    # Whole pages only, unless even the first page is longer than the limit.
                    if pages:
                        break
                    page_text, whole = page_text[:MAX_TEXT_CHARACTERS], False
                text += ("\n\n" if text else "") + page_text
                pages.append(
                    {
                        **{k: v for k, v in result.items() if k != "text"},
                        "start_char": start,
                        "end_char": len(text),
                    }
                )
        finally:
            document.close()
    if not any(page["method"] != "blank" for page in pages):
        raise PdfError("PDF contains no readable content; original retained.")
    reading = {
        "text": text,
        "pages": pages,
        "page_count": len(pages),
        "parser_version": PDF_VERSION,
        "sha256": digest,
    }
    if len(pages) < count or not whole:
        reading.update(pdf_pages=count, cut=cut_notice(len(pages), count, whole=whole))
    return reading
