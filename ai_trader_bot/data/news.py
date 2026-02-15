from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

POSITIVE_WORDS = {
    "beats",
    "beat",
    "growth",
    "surge",
    "record",
    "strong",
    "upgrade",
    "bullish",
    "breakthrough",
    "expands",
    "partnership",
    "profit",
    "outperform",
    "demand",
    "upside",
}

NEGATIVE_WORDS = {
    "miss",
    "misses",
    "weak",
    "downgrade",
    "lawsuit",
    "probe",
    "delay",
    "cuts",
    "cut",
    "layoffs",
    "decline",
    "bearish",
    "risk",
    "warning",
    "downside",
}


@dataclass(frozen=True)
class NewsItem:
    title: str
    description: str
    source: str
    link: str
    published_at: datetime | None
    source_type: str = "news"
    author: str = ""
    content: str = ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").strip()


def fetch_google_news_items(
    query: str,
    *,
    lookback_hours: int,
    max_items: int,
    timeout_seconds: float,
) -> list[NewsItem]:
    query_block = quote_plus(f"{query} when:{lookback_hours}h")
    url = f"https://news.google.com/rss/search?q={query_block}&hl=en-US&gl=US&ceid=US:en"
    request = Request(url=url, headers={"User-Agent": "ai-autotrader/0.1"})
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")

    root = ET.fromstring(body)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    items: list[NewsItem] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue

        description = _strip_html(item.findtext("description") or "")
        source = (item.findtext("source") or "").strip()
        link = (item.findtext("link") or "").strip()

        pub_date = item.findtext("pubDate")
        published_at: datetime | None = None
        if pub_date:
            try:
                published = parsedate_to_datetime(pub_date)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < cutoff:
                    continue
                published_at = published
            except Exception:
                pass

        items.append(
            NewsItem(
                title=title,
                description=description,
                source=source,
                link=link,
                published_at=published_at,
                source_type="news",
            )
        )
        if len(items) >= max_items:
            break

    return items


def _headline_score(headline: str) -> float:
    words = re.findall(r"[a-zA-Z']+", headline.lower())
    if not words:
        return 0.0

    positive = sum(1 for token in words if token in POSITIVE_WORDS)
    negative = sum(1 for token in words if token in NEGATIVE_WORDS)

    if positive == 0 and negative == 0:
        return 0.0

    return (positive - negative) / (positive + negative)


def sentiment_score(headlines: list[str]) -> float:
    if not headlines:
        return 0.0

    scored = [_headline_score(headline) for headline in headlines]
    aggregate = sum(scored) / len(scored)
    return max(-1.0, min(1.0, aggregate))


def source_weighted_sentiment(
    items: list[NewsItem],
    *,
    source_multipliers: dict[str, float] | None = None,
) -> tuple[float, dict[str, float], dict[str, int]]:
    if not items:
        return 0.0, {}, {}

    scores_by_source: dict[str, list[float]] = {}
    counts_by_source: dict[str, int] = {}
    for item in items:
        source_type = (item.source_type or "unknown").strip().lower() or "unknown"
        score = _headline_score(item.title)
        scores_by_source.setdefault(source_type, []).append(score)
        counts_by_source[source_type] = counts_by_source.get(source_type, 0) + 1

    sentiment_by_source: dict[str, float] = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for source_type, scores in scores_by_source.items():
        aggregate = sum(scores) / len(scores) if scores else 0.0
        sentiment_by_source[source_type] = max(-1.0, min(1.0, aggregate))
        count = counts_by_source.get(source_type, 0)
        multiplier = 1.0
        if source_multipliers is not None:
            raw = source_multipliers.get(source_type, 1.0)
            multiplier = max(0.10, min(float(raw), 3.0))
        weight = count * multiplier
        weighted_sum += sentiment_by_source[source_type] * weight
        total_weight += weight

    if total_weight <= 0:
        return 0.0, sentiment_by_source, counts_by_source

    aggregate = weighted_sum / total_weight
    return max(-1.0, min(1.0, aggregate)), sentiment_by_source, counts_by_source
