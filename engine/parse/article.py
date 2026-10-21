"""Extract an HTML article without an LLM; never fetch links found inside it."""

from dataclasses import dataclass
import json

from lxml import html, etree
from trafilatura import bare_extraction

from engine.parse.web import WebError

ARTICLE_PARSER_VERSION = "article-v1"


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


def parse_article(content: bytes, url: str) -> Article:
    try:
        document = bare_extraction(content, url=url, with_metadata=True,
                                   include_comments=False, include_tables=True,
                                   favor_precision=True, date_extraction_params={"extensive_search": False})
        if document is None or not isinstance(document.text, str) or len(document.text.strip()) < 100:
            raise WebError("Could not extract a substantive article; paste the text if the page requires login or JavaScript.")
        return Article(document.text.strip(), document.title or None, document.author or None,
                       document.sitename or None, _publication_date(content))
    except WebError:
        raise
    except (ValueError, TypeError, AttributeError, etree.LxmlError):
        raise WebError("Could not parse the article HTML; its snapshot was retained.") from None
