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
            if not 1 <= count <= MAX_PAGES:
                raise PdfError(
                    f"PDF has {count} pages; this version supports up to {MAX_PAGES}. Split it into smaller PDFs; original retained."
                )
            for index in range(count):
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
                            if textpage.count_chars() > MAX_TEXT_CHARACTERS:
                                raise PdfError(
                                    f"Page {number} exceeds the text limit; original retained without truncation."
                                )
                            native = (
                                textpage.get_text_range().replace("\r\n", "\n").strip()
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
                if text:
                    text += "\n\n"
                start = len(text)
                text += f"PDF page {number}:\n" + result["text"]
                if len(text) > MAX_TEXT_CHARACTERS:
                    raise PdfError(
                        "PDF exceeds the 50,000-character limit; split it into smaller files. Original and completed page readings retained; nothing truncated."
                    )
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
    return {
        "text": text,
        "pages": pages,
        "page_count": count,
        "parser_version": PDF_VERSION,
        "sha256": digest,
    }
