import asyncio
import re
from functools import cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.config import load_public_website_search_settings
from src.models import SearchResult, SearchResults, SearchSource
from src.services.embeddings import generate_embedding
from src.utils.vectors import cosine_similarity


async def search(query: str, course_id: str) -> SearchResults:
    del course_id  # Public policy pages are shared by all courses.
    settings = _get_settings()
    query_embedding = await generate_embedding(query)
    chunks: list[tuple[float, SearchResult]] = []

    for url in settings["public_website_urls"]:
        try:
            html = await asyncio.to_thread(_fetch_webpage, url)
            title = _extract_title(html) or urlparse(url).netloc or url
            text = _extract_visible_text(html)

            for chunk_text in _split_into_chunks(text):
                chunk_embedding = await generate_embedding(chunk_text)
                chunks.append(
                    (
                        cosine_similarity(query_embedding, chunk_embedding),
                        SearchResult(
                            source=SearchSource.UNIVERSITY_POLICIES,
                            content=chunk_text,
                            title=title,
                            source_url=url,
                            excerpt=chunk_text[:220],
                        ),
                    )
                )
        except (HTTPError, URLError, TimeoutError, ValueError):
            continue

    chunks.sort(key=lambda item: item[0], reverse=True)
    results = [result for _, result in chunks[: settings["top_k"]]]
    return SearchResults(query=query, results=results)


@cache
def _get_settings() -> dict[str, Any]:
    return load_public_website_search_settings()


def _fetch_webpage(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; on-course-agent/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=10) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _normalize_whitespace(_strip_html_tags(match.group(1))) or None


def _extract_visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    return _normalize_whitespace(_strip_html_tags(html))


def _split_into_chunks(text: str, max_chars: int = 700, overlap: int = 120) -> list[str]:
    if not text:
        return []

    paragraphs = [
        part.strip()
        for part in re.split(r"\n{2,}|(?<=[.!?])\s+", text)
        if part.strip()
    ]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                chunks.append(_normalize_whitespace(paragraph[start : start + max_chars]))
                start += max_chars - overlap
            continue

        if not current:
            current = paragraph
        elif len(current) + 1 + len(paragraph) <= max_chars:
            current = f"{current} {paragraph}"
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)
    return [
        chunk
        for chunk in (_normalize_whitespace(chunk) for chunk in chunks)
        if chunk
    ]


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
