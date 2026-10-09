"""Turn web-app inputs (pasted text, an uploaded PDF/DOCX, an article URL) into text.

Uploads are parsed in memory and never written to disk.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Optional, Tuple
from xml.etree import ElementTree

MAX_UPLOAD_BYTES = 10_000_000
MAX_INPUT_CHARS = 60_000
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class InputError(ValueError):
    """Raised with a message that is safe to show the user."""


def _cap(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        raise InputError("no readable text found")
    return text[:MAX_INPUT_CHARS]


def text_from_pdf(data: bytes) -> str:
    from odar.retrieval import _pdf_to_text

    text = _pdf_to_text(data)
    if text.startswith("\x00pdf-error:"):
        raise InputError(text[1:].replace("pdf-error:", "PDF error:").strip())
    return _cap(text)


def text_from_docx(data: bytes) -> str:
    """Paragraph text plus hyperlink targets (so cited URLs survive)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        info = archive.getinfo("word/document.xml")
        if info.file_size > 30_000_000:  # zip-bomb guard
            raise InputError("DOCX is too large once unpacked")
        root = ElementTree.fromstring(archive.read(info))
        links = {}
        try:
            rels = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
            for rel in rels:
                if rel.get("TargetMode") == "External":
                    links[rel.get("Id")] = rel.get("Target", "")
        except KeyError:
            pass
    except InputError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"could not read DOCX ({type(exc).__name__})") from exc
    paragraphs = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append(" ")
            elif node.tag == f"{_W}hyperlink":
                target = links.get(node.get(f"{_REL}id") or "")
                if target:
                    label = "".join(t.text or "" for t in node.iter(f"{_W}t"))
                    if target not in label:
                        parts.append(f" ({target}) ")
        paragraphs.append("".join(parts))
    return _cap("\n".join(paragraphs))


def text_from_upload(filename: str, data: bytes) -> Tuple[str, str]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise InputError("file is larger than 10 MB")
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return text_from_pdf(data), "pdf"
    if name.endswith(".docx") or data[:2] == b"PK":
        return text_from_docx(data), "docx"
    if name.endswith((".txt", ".md")):
        return _cap(data.decode("utf-8", "replace")), "text"
    raise InputError("upload a PDF, DOCX, TXT or MD file")


_GDOC = re.compile(r"^https://docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})")


def html_to_text_with_links(markup: str) -> str:
    """Visible text with each hyperlink kept as ``text (url)`` so citations survive."""
    import html as htmlmod
    from urllib.parse import parse_qs, urlparse

    def link(m: "re.Match[str]") -> str:
        href = htmlmod.unescape(m.group(1))
        parsed = urlparse(href)
        if parsed.netloc.endswith("google.com") and parsed.path == "/url":
            href = parse_qs(parsed.query).get("q", [href])[0]
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not href.startswith(("http://", "https://")):
            return label
        return f"{label} ({href})" if label and label != href else href

    body = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", markup)
    body = re.sub(r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', link, body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", body)
    body = htmlmod.unescape(re.sub(r"<[^>]+>", "", body))
    lines = [" ".join(line.split()) for line in body.splitlines()]
    return "\n".join(line for line in lines if line)


def text_from_google_doc(url: str, get: Optional[Any] = None) -> Tuple[str, str]:
    """A Google Doc shared as "anyone with the link" via its HTML export (links kept)."""
    match = _GDOC.match(url.strip())
    if not match:
        raise InputError("not a Google Docs document link")
    export = f"https://docs.google.com/document/d/{match.group(1)}/export?format=html"
    if get is None:
        import requests

        def get(u: str) -> Tuple[int, str]:
            resp = requests.get(u, timeout=20, allow_redirects=True, headers={"User-Agent": "ODAR/1.0"})
            final = resp.url or u
            if "accounts.google.com" in final:
                return 403, ""
            return resp.status_code, resp.text

    status, markup = get(export)
    if status != 200 or not markup:
        raise InputError(
            "couldn't open that Google Doc. Share it as 'Anyone with the link can view', "
            "or use File > Download > .docx and upload it"
        )
    title = re.search(r"(?is)<title>(.*?)</title>", markup)
    text = html_to_text_with_links(markup)
    if len(text) < 20:
        raise InputError("that Google Doc looks empty")
    return _cap(text), (title.group(1).strip() if title else "Google Doc")


def text_from_url(url: str, extractor: Optional[Any] = None) -> Tuple[str, str]:
    """Fetch an article through the SSRF-guarded extractor; returns (text, title)."""
    from odar.retrieval import PageExtractor

    if _GDOC.match(url.strip()):
        return text_from_google_doc(url)

    extractor = extractor or PageExtractor(max_chars=MAX_INPUT_CHARS, allow_pdf=True)
    page = extractor.extract(url.strip())
    if not page.ok:
        raise InputError(f"could not read that page: {page.error or 'no text'}")
    return _cap(page.text), page.title or url
