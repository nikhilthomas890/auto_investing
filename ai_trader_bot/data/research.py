from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from .news import NewsItem, fetch_google_news_items

_SEC_TICKER_MAP: dict[str, str] | None = None


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_datetime(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return _to_utc(datetime.fromisoformat(normalized))
    except ValueError:
        pass

    try:
        return _to_utc(parsedate_to_datetime(text))
    except Exception:
        pass

    try:
        date_only = datetime.strptime(text[:10], "%Y-%m-%d")
        return date_only.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fetch_url_text(url: str, *, timeout_seconds: float, user_agent: str) -> str:
    request = Request(url=url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
    return body.decode("utf-8", errors="ignore")


def _fetch_url_json(url: str, *, timeout_seconds: float, user_agent: str) -> dict | list | None:
    raw = _fetch_url_text(url, timeout_seconds=timeout_seconds, user_agent=user_agent)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def fetch_article_text(
    url: str,
    *,
    timeout_seconds: float,
    max_chars: int,
    user_agent: str = "ai-autotrader/0.2",
) -> str:
    target = (url or "").strip()
    if not target:
        return ""

    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"}:
        return ""

    try:
        raw = _fetch_url_text(target, timeout_seconds=timeout_seconds, user_agent=user_agent)
    except Exception:
        return ""

    text = _strip_html(raw)
    return _truncate(text, max_chars)


def enrich_with_full_text(
    items: list[NewsItem],
    *,
    timeout_seconds: float,
    max_chars: int,
    user_agent: str = "ai-autotrader/0.2",
) -> list[NewsItem]:
    enriched: list[NewsItem] = []
    for item in items:
        content = item.content.strip()
        if not content and item.link:
            content = fetch_article_text(
                item.link,
                timeout_seconds=timeout_seconds,
                max_chars=max_chars,
                user_agent=user_agent,
            )
        enriched.append(
            NewsItem(
                title=item.title,
                description=item.description,
                source=item.source,
                link=item.link,
                published_at=item.published_at,
                source_type=item.source_type,
                author=item.author,
                content=content,
            )
        )
    return enriched


def _load_sec_ticker_map(*, timeout_seconds: float, user_agent: str) -> dict[str, str]:
    global _SEC_TICKER_MAP
    if _SEC_TICKER_MAP is not None:
        return _SEC_TICKER_MAP

    payload = _fetch_url_json(
        "https://www.sec.gov/files/company_tickers.json",
        timeout_seconds=timeout_seconds,
        user_agent=user_agent,
    )
    mapping: dict[str, str] = {}

    if isinstance(payload, dict):
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            cik = row.get("cik_str")
            if not ticker or not isinstance(cik, (int, float, str)):
                continue
            digits = re.sub(r"\D", "", str(cik))
            if digits:
                mapping[ticker] = digits.zfill(10)

    _SEC_TICKER_MAP = mapping
    return mapping


def fetch_sec_filings_items(
    symbol: str,
    *,
    lookback_hours: int,
    max_items: int,
    timeout_seconds: float,
    user_agent: str,
    forms: list[str],
    include_full_text: bool,
    max_content_chars: int,
) -> list[NewsItem]:
    ticker = symbol.strip().upper()
    if not ticker:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    forms_filter = {form.strip().upper() for form in forms if form.strip()}
    try:
        cik_map = _load_sec_ticker_map(timeout_seconds=timeout_seconds, user_agent=user_agent)
    except Exception as exc:
        logging.warning("SEC ticker map fetch failed: %s", exc)
        return []

    cik = cik_map.get(ticker)
    if not cik:
        return []

    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    payload = _fetch_url_json(submissions_url, timeout_seconds=timeout_seconds, user_agent=user_agent)
    if not isinstance(payload, dict):
        return []

    recent = payload.get("filings", {}).get("recent")
    if not isinstance(recent, dict):
        return []

    forms_arr = recent.get("form")
    dates_arr = recent.get("filingDate")
    accession_arr = recent.get("accessionNumber")
    primary_doc_arr = recent.get("primaryDocument")
    if not all(isinstance(arr, list) for arr in (forms_arr, dates_arr, accession_arr, primary_doc_arr)):
        return []

    total = min(len(forms_arr), len(dates_arr), len(accession_arr), len(primary_doc_arr))
    items: list[NewsItem] = []
    for idx in range(total):
        form = str(forms_arr[idx] or "").strip().upper()
        if forms_filter and form not in forms_filter:
            continue

        filed_at = _parse_datetime(str(dates_arr[idx] or ""))
        if filed_at is None or filed_at < cutoff:
            continue

        accession = str(accession_arr[idx] or "").strip()
        primary_doc = str(primary_doc_arr[idx] or "").strip()
        accession_digits = accession.replace("-", "")
        filing_url = ""
        if accession_digits and primary_doc:
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_digits}/{quote_plus(primary_doc)}"
            )

        content = ""
        if include_full_text and filing_url:
            content = fetch_article_text(
                filing_url,
                timeout_seconds=timeout_seconds,
                max_chars=max_content_chars,
                user_agent=user_agent,
            )

        title = f"{ticker} filed {form} with the SEC"
        description = f"SEC filing date {filed_at.date().isoformat()}."
        items.append(
            NewsItem(
                title=title,
                description=description,
                source="SEC EDGAR",
                link=filing_url,
                published_at=filed_at,
                source_type="sec_filing",
                author="",
                content=content,
            )
        )
        if len(items) >= max_items:
            break

    return items


def fetch_earnings_transcript_items(
    symbol: str,
    *,
    lookback_hours: int,
    max_items: int,
    timeout_seconds: float,
    fmp_api_key: str,
    max_content_chars: int,
) -> list[NewsItem]:
    ticker = symbol.strip().upper()
    if not ticker or not fmp_api_key.strip():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    url = (
        f"https://financialmodelingprep.com/api/v3/earning_call_transcript/"
        f"{quote_plus(ticker)}?limit={max(1, max_items)}&apikey={quote_plus(fmp_api_key.strip())}"
    )
    payload = _fetch_url_json(url, timeout_seconds=timeout_seconds, user_agent="ai-autotrader/0.2")
    if not isinstance(payload, list):
        return []

    items: list[NewsItem] = []
    for row in payload:
        if not isinstance(row, dict):
            continue

        published_at = _parse_datetime(str(row.get("date") or ""))
        if published_at is None or published_at < cutoff:
            continue

        quarter = row.get("quarter")
        year = row.get("year")
        content = str(row.get("content") or "").strip()
        if not content:
            continue

        q_text = f" Q{quarter}" if isinstance(quarter, (int, float, str)) and str(quarter).strip() else ""
        y_text = f" {year}" if isinstance(year, (int, float, str)) and str(year).strip() else ""
        title = f"{ticker} earnings transcript{q_text}{y_text}".strip()
        preview = _truncate(content, 240)
        items.append(
            NewsItem(
                title=title,
                description=preview,
                source="FinancialModelingPrep",
                link=str(row.get("link") or "").strip(),
                published_at=published_at,
                source_type="earnings_transcript",
                author="",
                content=_truncate(content, max_content_chars),
            )
        )
        if len(items) >= max_items:
            break

    return items


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _extract_entry_text(entry: ET.Element, names: set[str]) -> str:
    for child in list(entry):
        if _local_name(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _extract_entry_link(entry: ET.Element) -> str:
    for child in list(entry):
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        if href:
            return href
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _extract_entry_author(entry: ET.Element) -> str:
    for child in list(entry):
        name = _local_name(child.tag)
        if name in {"creator", "author"}:
            if name == "author":
                nested = _extract_entry_text(child, {"name"})
                if nested:
                    return nested
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _social_entry_relevant(text: str, symbol: str, query: str) -> bool:
    haystack = text.lower()
    ticker = symbol.strip().lower()
    if not ticker:
        return False

    if re.search(rf"\b{re.escape(ticker)}\b", haystack) or f"${ticker}" in haystack:
        return True

    query_terms = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]{3,}", query)
        if token.lower() not in {"with", "from", "that", "this", "into", "while", "after", "before"}
    ]
    matches = sum(1 for token in query_terms if token in haystack)
    return matches >= 2


def fetch_social_feed_items(
    symbol: str,
    query: str,
    *,
    rss_urls: list[str],
    trusted_accounts: list[str],
    lookback_hours: int,
    max_items: int,
    timeout_seconds: float,
) -> list[NewsItem]:
    urls = [url.strip() for url in rss_urls if url.strip()]
    if not urls:
        return []

    trusted = [item.strip().lstrip("@").lower() for item in trusted_accounts if item.strip()]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    collected: list[NewsItem] = []

    for url in urls:
        try:
            body = _fetch_url_text(url, timeout_seconds=timeout_seconds, user_agent="ai-autotrader/0.2")
            root = ET.fromstring(body)
        except Exception as exc:
            logging.warning("Social feed fetch failed for %s: %s", url, exc)
            continue

        feed_source = _extract_entry_text(root, {"title"}) or urlparse(url).netloc or "Social Feed"
        entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
        for entry in entries:
            title = _extract_entry_text(entry, {"title"})
            description = _extract_entry_text(entry, {"description", "summary", "content"})
            link = _extract_entry_link(entry)
            author = _extract_entry_author(entry)

            published = (
                _parse_datetime(_extract_entry_text(entry, {"pubdate"}))
                or _parse_datetime(_extract_entry_text(entry, {"published"}))
                or _parse_datetime(_extract_entry_text(entry, {"updated"}))
            )
            if published is not None and published < cutoff:
                continue

            if trusted:
                if not author:
                    continue
                author_lc = author.lower()
                if not any(token in author_lc for token in trusted):
                    continue

            text_blob = f"{title}\n{description}"
            if not _social_entry_relevant(text_blob, symbol, query):
                continue

            collected.append(
                NewsItem(
                    title=title or f"{symbol} social update",
                    description=_truncate(_strip_html(description), 400),
                    source=feed_source,
                    link=link,
                    published_at=published,
                    source_type="social",
                    author=author,
                    content=_truncate(_strip_html(description), 1200),
                )
            )

            if len(collected) >= max_items:
                return collected

    return collected[:max_items]


def fetch_analyst_rating_items(
    symbol: str,
    *,
    lookback_hours: int,
    max_items: int,
    timeout_seconds: float,
    finnhub_api_key: str,
    fmp_api_key: str,
) -> list[NewsItem]:
    ticker = symbol.strip().upper()
    if not ticker:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    items: list[NewsItem] = []

    if fmp_api_key.strip():
        try:
            fmp_url = (
                f"https://financialmodelingprep.com/api/v3/grade/"
                f"{quote_plus(ticker)}?limit={max(1, max_items)}&apikey={quote_plus(fmp_api_key.strip())}"
            )
            payload = _fetch_url_json(fmp_url, timeout_seconds=timeout_seconds, user_agent="ai-autotrader/0.2")
            if isinstance(payload, list):
                for row in payload:
                    if not isinstance(row, dict):
                        continue
                    published = _parse_datetime(str(row.get("date") or ""))
                    if published is None or published < cutoff:
                        continue
                    firm = str(row.get("gradingCompany") or "").strip() or "Analyst"
                    action = str(row.get("action") or "rating update").strip()
                    previous = str(row.get("previousGrade") or "?").strip()
                    new_grade = str(row.get("newGrade") or "?").strip()
                    text = f"{firm} {action}: {previous} -> {new_grade}"
                    items.append(
                        NewsItem(
                            title=f"{ticker} analyst rating change",
                            description=text,
                            source="FinancialModelingPrep",
                            link="",
                            published_at=published,
                            source_type="analyst_rating",
                            author=firm,
                            content=text,
                        )
                    )
                    if len(items) >= max_items:
                        return items[:max_items]
        except Exception as exc:
            logging.warning("FMP analyst rating fetch failed for %s: %s", ticker, exc)

    if finnhub_api_key.strip() and len(items) < max_items:
        try:
            finnhub_url = (
                "https://finnhub.io/api/v1/stock/recommendation"
                f"?symbol={quote_plus(ticker)}&token={quote_plus(finnhub_api_key.strip())}"
            )
            payload = _fetch_url_json(finnhub_url, timeout_seconds=timeout_seconds, user_agent="ai-autotrader/0.2")
            if isinstance(payload, list):
                for row in payload:
                    if not isinstance(row, dict):
                        continue
                    published = _parse_datetime(str(row.get("period") or ""))
                    if published is None or published < cutoff:
                        continue
                    detail = (
                        f"strongBuy={int(row.get('strongBuy', 0) or 0)}, "
                        f"buy={int(row.get('buy', 0) or 0)}, "
                        f"hold={int(row.get('hold', 0) or 0)}, "
                        f"sell={int(row.get('sell', 0) or 0)}, "
                        f"strongSell={int(row.get('strongSell', 0) or 0)}"
                    )
                    items.append(
                        NewsItem(
                            title=f"{ticker} analyst consensus snapshot",
                            description=detail,
                            source="Finnhub",
                            link="",
                            published_at=published,
                            source_type="analyst_rating",
                            author="",
                            content=detail,
                        )
                    )
                    if len(items) >= max_items:
                        break
        except Exception as exc:
            logging.warning("Finnhub recommendation fetch failed for %s: %s", ticker, exc)

    return items[:max_items]


def _dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[NewsItem] = []
    for item in items:
        key = (
            item.source_type.strip().lower(),
            item.link.strip().lower(),
            item.title.strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def collect_research_items(
    symbol: str,
    query: str,
    *,
    news_lookback_hours: int,
    sec_lookback_hours: int,
    earnings_lookback_hours: int,
    social_lookback_hours: int,
    analyst_lookback_hours: int,
    max_items_per_source: int,
    total_items_cap: int,
    timeout_seconds: float,
    include_full_article_text: bool,
    article_text_max_chars: int,
    enable_sec_filings: bool,
    sec_user_agent: str,
    sec_forms: list[str],
    enable_earnings_transcripts: bool,
    fmp_api_key: str,
    earnings_transcript_max_chars: int,
    enable_social_feeds: bool,
    social_feed_rss_urls: list[str],
    trusted_social_accounts: list[str],
    enable_analyst_ratings: bool,
    finnhub_api_key: str,
) -> list[NewsItem]:
    items: list[NewsItem] = []

    try:
        news_items = fetch_google_news_items(
            query,
            lookback_hours=max(1, news_lookback_hours),
            max_items=max(1, max_items_per_source),
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        logging.warning("News lookup failed for %s: %s", symbol, exc)
        news_items = []

    if include_full_article_text and news_items:
        news_items = enrich_with_full_text(
            news_items,
            timeout_seconds=timeout_seconds,
            max_chars=max(200, article_text_max_chars),
        )
    items.extend(news_items)

    if enable_sec_filings:
        try:
            items.extend(
                fetch_sec_filings_items(
                    symbol,
                    lookback_hours=max(1, sec_lookback_hours),
                    max_items=max(1, max_items_per_source),
                    timeout_seconds=timeout_seconds,
                    user_agent=sec_user_agent.strip() or "ai-autotrader/0.2",
                    forms=sec_forms,
                    include_full_text=include_full_article_text,
                    max_content_chars=max(200, article_text_max_chars),
                )
            )
        except Exception as exc:
            logging.warning("SEC filings fetch failed for %s: %s", symbol, exc)

    if enable_earnings_transcripts:
        try:
            items.extend(
                fetch_earnings_transcript_items(
                    symbol,
                    lookback_hours=max(1, earnings_lookback_hours),
                    max_items=max(1, max_items_per_source),
                    timeout_seconds=timeout_seconds,
                    fmp_api_key=fmp_api_key,
                    max_content_chars=max(200, earnings_transcript_max_chars),
                )
            )
        except Exception as exc:
            logging.warning("Earnings transcript fetch failed for %s: %s", symbol, exc)

    if enable_social_feeds:
        try:
            items.extend(
                fetch_social_feed_items(
                    symbol,
                    query,
                    rss_urls=social_feed_rss_urls,
                    trusted_accounts=trusted_social_accounts,
                    lookback_hours=max(1, social_lookback_hours),
                    max_items=max(1, max_items_per_source),
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception as exc:
            logging.warning("Social feed fetch failed for %s: %s", symbol, exc)

    if enable_analyst_ratings:
        try:
            items.extend(
                fetch_analyst_rating_items(
                    symbol,
                    lookback_hours=max(1, analyst_lookback_hours),
                    max_items=max(1, max_items_per_source),
                    timeout_seconds=timeout_seconds,
                    finnhub_api_key=finnhub_api_key,
                    fmp_api_key=fmp_api_key,
                )
            )
        except Exception as exc:
            logging.warning("Analyst rating fetch failed for %s: %s", symbol, exc)

    items = _dedupe_items(items)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items.sort(key=lambda item: item.published_at or epoch, reverse=True)
    return items[: max(1, total_items_cap)]
