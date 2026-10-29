"""Extract an HTML article without an LLM; never fetch links found inside it."""

from dataclasses import dataclass
import json
import re

from lxml import html, etree
from trafilatura import bare_extraction

from engine.parse.web import WebError

ARTICLE_PARSER_VERSION = "article-v1"
# Site chrome trafilatura keeps after an article, by the line it starts with: an arXiv abstract page ends with its
# tool list and the arXivLabs blurb, which name arXiv and arXivLabs and nothing the paper is about.
CHROME = (re.compile(r"^Bibliographic and Citation Tools$", re.M),)


@dataclass(frozen=True)
class Article:
    text: str
    title: str | None
    author: str | None
    site_name: str | None
    publication_date: str | None


def _publication_date(content: bytes) -> str | None:
    """Only explicit published metadata; never infer event time from URL or body."""
    tree = html.fromstring(content)
    dates = tree.xpath('//meta[@property="article:published_time" or @name="datePublished" or @itemprop="datePublished"]/@content')
    if dates:
        return dates[0].strip() or None

    def find(node):
        if isinstance(node, dict):
            value = node.get("datePublished")
            if isinstance(value, str) and value.strip():
                return value.strip()
            for key in ("@graph", "mainEntity"):
                value = find(node.get(key))
                if value:
                    return value
        elif isinstance(node, list):
            for child in node:
                value = find(child)
                if value:
                    return value
        return None

    for script in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            value = find(json.loads(script))
        except ValueError:
            continue
        if value:
            return value
    return None


def citation_authors(content: bytes) -> list[str]:
    """Authors from citation_author tags (arXiv, journals), written "Last, First", as "First Last". Trafilatura
    splits on the comma and joins with "; ", which turns one author into two."""
    try:
        tree = html.fromstring(content)
    except (ValueError, etree.LxmlError):
        return []
    names = []
    for value in tree.xpath('//meta[@name="citation_author"]/@content'):
        last, _, first = value.strip().partition(",")
        name = f"{first.strip()} {last.strip()}".strip() if first.strip() else last.strip()
        if name and name not in names:
            names.append(name)
    return names


def chrome_start(text: str) -> int:
    """Where site chrome starts in an article's text; its length when there is none. Captures parsed before it was
    cut still hold it, so entity extraction reads only up to here too."""
    return min((m.start() for pattern in CHROME if (m := pattern.search(text))), default=len(text))


def _first(names: list[str]) -> str | None:
    return names[0] if names else None  # an item has one author: the first one listed


def parse_article(content: bytes, url: str) -> Article:
    try:
        document = bare_extraction(content, url=url, with_metadata=True,
                                   include_comments=False, include_tables=True,
                                   favor_precision=True, date_extraction_params={"extensive_search": False})
        text = document.text.strip() if document is not None and isinstance(document.text, str) else ""
        text = text[:chrome_start(text)].rstrip()
        if len(text) < 100:
            raise WebError("Could not extract a substantive article; paste the text if the page requires login or JavaScript.")
        return Article(text, document.title or None, _first(citation_authors(content)) or document.author or None,
                       document.sitename or None, _publication_date(content))
    except WebError:
        raise
    except (ValueError, TypeError, AttributeError, etree.LxmlError):
        raise WebError("Could not parse the article HTML; its snapshot was retained.") from None
