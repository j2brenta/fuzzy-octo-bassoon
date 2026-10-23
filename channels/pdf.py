"""PDF originals and derived text for Telegram uploads and public links."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from engine.parse.pdf import MAX_PDF_BYTES, PDF_VERSION, PdfError, pdf_signature, read_pdf
from store.media import LimitedBuffer, publish_original
from store.raw import CaptureResult, capture_text


def pdf_attachment(message):
    document = message.document
    return document if document and (document.mime_type == 'application/pdf' or (document.file_name or '').lower().endswith('.pdf')) else None


def save_pdf(content: bytes, vault: Path, fields: dict, *, channel='telegram', identity=None) -> Path:
    if len(content) > MAX_PDF_BYTES or not pdf_signature(content):
        raise PdfError('Send a valid PDF under 20 MB; your Telegram message or link remains available.')
    iid = hashlib.sha256(json.dumps(['pdf',channel,fields['source_id'],fields.get('message_id'),identity]).encode()).hexdigest()
    digest = hashlib.sha256(content).hexdigest()
    receipt = Path(vault)/'raw'/'pdfs'/f'{iid}.json'
    if receipt.exists():
        data,_ = checked_pdf(receipt)
        if data['sha256'] != digest:
            raise PdfError('This source already holds a different PDF; original preserved.')
        return receipt
    data = {'schema_version':1,'stage':'pdf_capture','item_id':iid,'sha256':digest,
            'channel':channel,'source':fields,'captured_at':datetime.now(timezone.utc).isoformat()}
    publish_original(receipt.parent/f'{digest}.pdf',content)
    publish_original(receipt,(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())
    return receipt


def checked_pdf(receipt: Path):
    receipt = Path(receipt)
    data = json.loads(receipt.read_text())
    digest = data.get('sha256')
    if (receipt.parent.name != 'pdfs' or receipt.parent.parent.name != 'raw'
        or not re.fullmatch(r'[a-f0-9]{64}', str(digest))
        or data.get('stage')!='pdf_capture' or data.get('item_id')!=receipt.stem):
        raise PdfError('Saved PDF identity is invalid; original preserved.')
    path = receipt.parent/f'{digest}.pdf'
    if path.stat().st_size > MAX_PDF_BYTES or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise PdfError('Saved PDF failed its integrity check; original preserved.')
    return data,path


async def capture_pdf(message, vault: Path, fields: dict) -> Path:
    attachment = pdf_attachment(message)
    if attachment is None or (attachment.file_size or 0)>MAX_PDF_BYTES:
        raise PdfError('Send a PDF under 20 MB; this file was not downloaded.')
    # Avoid a second Telegram download for a repeated delivery.
    iid=hashlib.sha256(json.dumps(['pdf','telegram',fields['source_id'],fields.get('message_id'),None]).encode()).hexdigest()
    receipt=Path(vault)/'raw'/'pdfs'/f'{iid}.json'
    if receipt.exists():
        data,_=await asyncio.to_thread(checked_pdf,receipt)
        saved=data['source']['channel_metadata'].get('document',{})
        if saved.get('file_unique_id')!=attachment.file_unique_id:
            raise PdfError('Message contains a different file; original preserved.')
        return receipt
    try:
        remote=await message.bot.get_file(attachment.file_id)
        if not remote.file_path or (remote.file_size or 0)>MAX_PDF_BYTES:
            raise PdfError('PDF cannot be downloaded within the 20 MB limit.')
        with LimitedBuffer(MAX_PDF_BYTES) as buffer:
            await message.bot.download_file(remote.file_path,destination=buffer,timeout=60)
            content=buffer.getvalue()
    except PdfError:raise
    except Exception:
        raise PdfError('Could not download the PDF from Telegram. Resend it; no capture success was recorded.') from None
    return await asyncio.to_thread(save_pdf,content,vault,fields)


def interpret_pdf(receipt: Path, *, reader=None) -> CaptureResult:
    receipt=Path(receipt)
    data,path=checked_pdf(receipt)
    vault=receipt.parent.parent.parent
    fields=data['source']
    # Derived identity includes PDF content/version, retaining older extractions on refresh.
    message_id=f"{fields.get('message_id') or receipt.stem}:{PDF_VERSION}:{data['sha256']}"
    iid=hashlib.sha256(json.dumps([data['channel'],fields['source_id'],message_id,None]).encode()).hexdigest()
    destination=vault/'raw'/f'{iid}.json'
    if destination.exists():
        from engine.parse import parse_capture
        parse_capture(destination)
        cached=json.loads(destination.read_text())
        if cached.get('channel_metadata',{}).get('pdf_reading',{}).get('sha256')!=data['sha256']:
            raise PdfError('Saved text belongs to a different PDF; original preserved.')
        return CaptureResult(destination,iid,True)
    reading=read_pdf(path,vault/'documents'/'pdf-pages',reader=reader)
    text=reading['text']
    caption=(fields['channel_metadata'].get('caption') or '').strip()
    if caption:
        text+='\n\nTelegram caption (sender supplied):\n'+caption
    metadata=dict(fields['channel_metadata'])
    metadata.pop('caption',None);metadata.pop('caption_entities',None)
    metadata['pdf_reading']={**{k:v for k,v in reading.items() if k!='text'},
       'pdf_path':str(path.relative_to(vault)),'receipt_path':str(receipt.relative_to(vault)),
       'receipt_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}
    return capture_text(text,vault,channel=data['channel'],**{**fields,'message_id':message_id,'channel_metadata':metadata})


def validate_pdf_reference(raw_path: Path, record: dict):
    ref=(record.get('channel_metadata') or {}).get('pdf_reading')
    if ref is None:return None
    vault=raw_path.parent.parent.resolve()
    try:
        receipt=vault/ref['receipt_path']
        if (receipt.parent.resolve()!=vault/'raw'/'pdfs' or not re.fullmatch(r'[a-f0-9]{64}\.json',receipt.name)
            or not receipt.resolve().is_relative_to(vault)):
            raise ValueError
        data,path=checked_pdf(receipt)
        if (not path.resolve().is_relative_to(vault) or ref['pdf_path']!=str(path.relative_to(vault))
            or data['sha256']!=ref['sha256'] or ref['receipt_sha256']!=hashlib.sha256(receipt.read_bytes()).hexdigest()):
            raise ValueError
        previous=0
        for number,page in enumerate(ref['pages'],1):
            start,end=page['start_char'],page['end_char']
            if (page['page']!=number or page['pdf_sha256']!=ref['sha256'] or type(start) is not int or type(end) is not int
                or start<previous or end<=start or end>len(record['text'])
                or not record['text'][start:end].startswith(f'PDF page {number}:\n')):raise ValueError
            previous=end
        if len(ref['pages'])!=ref['page_count']:raise ValueError
        for key in ['source_id','author_hint','forwarded_by','published_at']:
            if data['source'].get(key)!=record.get(key):raise ValueError
    except (OSError,ValueError,KeyError,TypeError,PdfError):
        raise ValueError('PDF provenance failed validation; original retained.') from None
    return ref
