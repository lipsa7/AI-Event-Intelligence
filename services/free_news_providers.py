"""
Free news discovery providers for AI Event Intelligence.

Location:
    services/free_news_providers.py

Providers (all return the same result shape as NewsAPIProvider.search):
    GDELTDocProvider  - GDELT DOC 2.0 article search. Free, no key, global,
                        ~3 months back, <= 250 results per query. Rate limit:
                        one request per 5 seconds per IP, enforced here.
                        Returns headlines only; its date is when GDELT saw the
                        article (published_at_basis = "gdelt_seen_date").
    RSSFeedProvider   - Feeds listed in config/news_feeds.txt (or NEWS_RSS_FEEDS).
                        Free, unlimited. Not searchable at the source, so items
                        are matched to the query locally. Feeds are cached.
    ExaProvider       - Exa neural web search (EXA_API_KEY). Free monthly
                        credit; used mainly for finding past cases by meaning.
                        Page text is NOT requested (that is billed separately);
                        the pipeline fetches publisher pages itself.

Discovery results are links and headlines only. They are never evidence: the
orchestrator fetches the publisher page and extracts from the original text.
Every result carries `provider` so the source of a link is always visible.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

SERVICE_VERSION = "free-news-providers-v1.0"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0 Safari/537.36 EventIntelligence/2.5"
)

GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
EXA_SEARCH_ENDPOINT = "https://api.exa.ai/search"


class FreeNewsProviderError(RuntimeError):
    """A free provider failed (network, format or provider error)."""


class FreeNewsRateLimitError(FreeNewsProviderError):
    """A provider refused the request because of its rate limit."""


class FreeNewsOutOfCoverageError(FreeNewsProviderError):
    """The requested dates are outside what this source can search. Not a
    failure: the source was not asked (recorded as OUT_OF_COVERAGE)."""


class FreeNewsConfigurationError(FreeNewsProviderError):
    """A provider is not configured (e.g. missing key or feed list)."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.casefold()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_date(value: Any) -> Optional[datetime]:
    text = _clean(value)
    if not text:
        return None
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime  # RSS dates
        parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def _window(kwargs: Mapping[str, Any], default_days: float) -> tuple[datetime, datetime]:
    """(start, end) in UTC. A date-only end ('2026-10-04') covers that whole day."""
    now = datetime.now(timezone.utc)
    raw_end = _clean(kwargs.get("to_date") or kwargs.get("to"))
    start = _parse_date(kwargs.get("from_date") or kwargs.get("from"))
    end = _parse_date(raw_end)
    if end is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_end):
        end = end + timedelta(days=1) - timedelta(seconds=1)
    end = (end or now).astimezone(timezone.utc)
    start = (start or (end - timedelta(days=default_days))).astimezone(timezone.utc)
    return start, end


# ----------------------------------------------------------------------
# Query handling shared by providers
# ----------------------------------------------------------------------

AI_TOPIC_SYNONYMS = (
    "artificial intelligence", "ai", "a.i.", "generative ai", "genai", "machine learning",
    "llm", "llms", "large language model", "large language models", "ai model", "ai models",
    "ai agent", "ai agents", "agentic ai", "chatbot", "chatbots",
)


def parse_query_groups(query: str) -> list[list[str]]:
    """'(a b) AND ("C D" OR e) AND (f OR g)' -> [['a b'], ['c d', 'e'], ['f', 'g']].

    A query without AND is one group holding the whole query as a phrase.
    Terms are lower-cased and unquoted.
    """
    text = _clean(query)
    if not text:
        return []
    parts = re.split(r"\)\s+AND\s+\(", text) if " AND " in text else [text]
    groups: list[list[str]] = []
    for part in parts:
        part = part.strip().strip("()").strip()
        terms = [
            term.strip().strip('"').strip().casefold()
            for term in re.split(r"\s+OR\s+", part)
            if term.strip().strip('"').strip()
        ]
        if terms:
            groups.append(terms)
    return groups


KNOWN_PHRASES = frozenset({
    "artificial intelligence", "generative ai", "machine learning", "large language model",
    "large language models", "agentic ai", "ai agents", "data center", "data centers",
})
TOPIC_STOPWORDS = frozenset({"in", "of", "the", "and", "for", "on", "to", "a", "an", "with", "at", "by"})


def topic_words(term: str) -> list[str]:
    """A multi-word topic like 'AI in retail' -> ['ai', 'retail'] (each required);
    known phrases ('artificial intelligence') stay whole."""
    term = term.casefold().strip()
    if term in KNOWN_PHRASES or len(term.split()) == 1:
        return [term]
    return [word for word in re.findall(r"[a-z0-9][a-z0-9.\-]*", term) if word not in TOPIC_STOPWORDS]


def _term_present(term: str, text: str) -> bool:
    """Whole-word match in already case-folded text. 'artificial intelligence'
    and 'AI' also match common AI wordings; single words allow simple
    inflections (bank -> banks/banking)."""
    term = term.casefold()
    if term in {"artificial intelligence", "ai", "a.i."}:
        return any(
            re.search(rf"(?<!\w){re.escape(synonym)}(?!\w)", text) is not None
            for synonym in AI_TOPIC_SYNONYMS
        )
    if len(term.split()) > 1:
        pattern = r"\s+".join(rf"{re.escape(_stem(word))}\w{{0,4}}" for word in term.split())
        return re.search(rf"(?<!\w){pattern}(?!\w)", text) is not None
    return re.search(rf"(?<!\w){re.escape(_stem(term))}\w{{0,4}}(?!\w)", text) is not None


def _stem(word: str) -> str:
    """Crude suffix strip so 'chips'~'chip', 'lending'~'lenders',
    'batteries'~'battery', 'policy'~'policies'. Keeps at least 4 letters
    ('power' stays)."""
    for suffix in ("ies", "ers", "ing", "es", "ed", "er", "s", "y"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


# Words that carry no topic in a plain research phrase.
QUERY_FILLER_WORDS = frozenset({
    "and", "or", "not", "the", "of", "in", "on", "to", "for", "with", "at", "by", "from",
    "into", "vs", "versus", "between", "how", "why", "what", "when", "which", "does", "do",
    "is", "are", "be", "than", "more", "less", "its", "their", "after", "before", "over",
})


def plain_query_terms(query: str) -> Optional[list[str]]:
    """Content words of a plain (non-boolean) phrase with more than three of
    them, e.g. a research query like "third party model auditing intellectual
    property leakage risk". None for anchored queries ("(AI) AND (...)") and
    short topics ("AI in retail"), which keep the every-word rule."""
    text = _clean(query)
    if not text or re.search(r"\s(AND|OR)\s", text) or "(" in text:
        return None
    groups = parse_query_groups(text)
    if len(groups) != 1 or len(groups[0]) != 1:
        return None
    words = [
        word.strip(".-") for word in topic_words(groups[0][0])
        if word.strip(".-") not in QUERY_FILLER_WORDS
        and (len(word.strip(".-")) >= 3 or word.strip(".-") == "ai")
    ]
    words = list(dict.fromkeys(word for word in words if word))
    return words if len(words) > 3 else None


def plain_match_share() -> float:
    """Share of a plain phrase's content words a headline must contain
    (PLAIN_QUERY_MATCH_SHARE, default 0.4). Headlines are short; the full
    document is reviewed later, so this stage favours recall and ranks."""
    return _env_float("PLAIN_QUERY_MATCH_SHARE", 0.4, 0.1, 1.0)


def _term_weight(term: str) -> float:
    """Longer words are usually more specific ("underwriting" vs "risk")."""
    return 1.5 if len(term) >= 7 else 1.0


def _plain_match(terms: list[str], haystack: str) -> tuple[float, bool, int]:
    """(weighted share of terms present, adjacent query pair found as a phrase,
    number of terms present). Terms use stem/plural-tolerant matching."""
    present = [term for term in terms if _term_present(term, haystack)]
    share = sum(_term_weight(t) for t in present) / sum(_term_weight(t) for t in terms)
    phrase = any(
        _term_present(f"{first} {second}", haystack)
        for first, second in zip(terms, terms[1:])
        if first != "ai" and second != "ai"
    )
    return share, phrase, len(present)


def query_match_score(query: str, text: str) -> float:
    """1.0/0.0 for anchored queries. For plain phrases: weighted share of
    content words, plus 0.25 when two adjacent query words appear as a phrase
    (e.g. "intellectual property"), capped at 1."""
    haystack = _clean(text).casefold()
    terms = plain_query_terms(query)
    if terms is None:
        return 1.0 if query_matches(query, text) else 0.0
    share, phrase, _ = _plain_match(terms, haystack)
    return min(1.0, share + (0.25 if phrase else 0.0))


def query_matches(query: str, text: str) -> bool:
    """Every AND-group must have at least one term in the text (case-insensitive).
    "artificial intelligence"/"AI" also match common AI wordings.

    A plain phrase of more than three content words (a research query) is
    accepted when two adjacent query words appear as a phrase, or when the
    weighted share of its words present reaches PLAIN_QUERY_MATCH_SHARE
    (default 0.4); at least two words must be present either way."""
    haystack = _clean(text).casefold()
    terms = plain_query_terms(query)
    if terms is not None:
        share, phrase, count = _plain_match(terms, haystack)
        return count >= 2 and (phrase or share >= plain_match_share())
    groups = parse_query_groups(query)
    if not groups:
        return False
    # The first group is the topic: a multi-word topic needs each of its words.
    if len(groups[0]) == 1:
        words = topic_words(groups[0][0])
        if not words or not all(_term_present(word, haystack) for word in words):
            return False
        groups = groups[1:]
    return all(any(_term_present(term, haystack) for term in group) for group in groups)


def _gdelt_term(term: str) -> str:
    """One search term in a form GDELT accepts. GDELT rejects punctuation in
    keywords ("post-training" gave "illegal character"), so punctuation
    becomes a space and multi-word terms are quoted phrases. "" if too short."""
    words = re.sub(r"[^a-z0-9 ]+", " ", term.casefold()).split()
    if not words:
        return ""
    if len(words) == 1:
        return words[0] if len(words[0]) >= 3 else ""
    return '"' + " ".join(words) + '"'


def to_gdelt_query(query: str) -> str:
    """Translate a discovery query to GDELT DOC syntax.

    GDELT: space = AND, OR only inside parentheses, phrases in quotes, and very
    short keywords are rejected, so a bare 'AI' becomes AI phrases.
    A plain research phrase becomes an OR of its content words: requiring all
    of them (GDELT's default) finds nothing; the provider then ranks and
    filters the results by how many words each headline contains.
    """
    plain = plain_query_terms(query)
    if plain is not None:
        terms: list[str] = []
        for word in plain:
            if word in {"ai", "a.i."}:
                terms.extend(['"artificial intelligence"', '"generative AI"'])
            else:
                rendered_term = _gdelt_term(word)
                if rendered_term:
                    terms.append(rendered_term)
        terms = list(dict.fromkeys(terms))
        if len(terms) < 2:
            raise FreeNewsProviderError(f"query has too few GDELT-searchable words: {query!r}")
        return "(" + " OR ".join(terms) + ")"
    groups = parse_query_groups(query)
    if groups and len(groups[0]) == 1 and len(topic_words(groups[0][0])) > 1:
        words = topic_words(groups[0][0])
        topic_groups: list[list[str]] = []
        index = 0
        while index < len(words):
            word = words[index]
            if len(word) < 3 and word not in {"ai", "a.i."}:
                # GDELT rejects very short keywords: keep it as a phrase with
                # its neighbour ("EV batteries") rather than dropping it.
                if index + 1 < len(words):
                    topic_groups.append([f"{word} {words[index + 1]}"])
                    index += 2
                    continue
                if topic_groups and len(topic_groups[-1]) == 1:
                    topic_groups[-1] = [f"{topic_groups[-1][0]} {word}"]
                    index += 1
                    continue
            topic_groups.append([word])
            index += 1
        groups = topic_groups + groups[1:]
    rendered: list[str] = []
    for group in groups:
        terms: list[str] = []
        for term in group:
            if term in {"ai", "a.i."}:
                terms.extend(['"artificial intelligence"', '"generative AI"', '"AI model"'])
            else:
                rendered_term = _gdelt_term(term)
                if rendered_term:
                    terms.append(rendered_term)
        terms = list(dict.fromkeys(terms))
        if not terms:
            # Every AND-group is required. Dropping one GDELT cannot search
            # would silently broaden the query, so fail visibly instead (the
            # other providers still answer this query).
            raise FreeNewsProviderError(
                f"GDELT cannot search the short term(s) {group!r} in query {query!r}"
            )
        rendered.append(terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")")
    return " ".join(rendered)


def to_natural_query(query: str) -> str:
    """Boolean anchored query -> plain words for neural search engines."""
    groups = parse_query_groups(query)
    return " ".join(term for group in groups for term in group[:3])[:300]


def _result(
    *,
    provider: str,
    url: str,
    title: str,
    snippet: str = "",
    published: Optional[datetime] = None,
    published_basis: str = "publisher",
    source_name: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    item = {
        "url": url,
        "canonical_url": url,
        "title": _clean(title),
        "snippet": _clean(snippet)[:1200],
        "description": _clean(snippet)[:1200],
        "domain": _domain(url),
        "published_at": _iso(published) if published else "",
        "published_at_basis": published_basis if published else "unknown",
        "source_name": _clean(source_name),
        "provider": provider,
        "discovery_provider": provider,
    }
    if extra:
        item.update(extra)
    return item


Transport = Callable[[Request, float], tuple[int, bytes, Mapping[str, str]]]


def _default_transport(request: Request, timeout: float) -> tuple[int, bytes, Mapping[str, str]]:
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 200) or 200), response.read(8_000_000), dict(response.headers)
    except HTTPError as error:
        body = b""
        try:
            body = error.read(200_000)
        except Exception:
            pass
        return int(error.code), body, dict(getattr(error, "headers", {}) or {})


# ----------------------------------------------------------------------
# GDELT DOC 2.0
# ----------------------------------------------------------------------

class GDELTDocProvider:
    """GDELT DOC 2.0 article list. One request per GDELT_MIN_INTERVAL_SECONDS
    (default 6) across the whole process; on refusal waits and retries
    GDELT_RETRIES times (10 s, 20 s ...) before raising FreeNewsRateLimitError."""

    provider_name = "gdelt"
    _lock = threading.Lock()
    _last_request_at = 0.0
    _cooldown_until = 0.0

    def __init__(
        self,
        *,
        language: str = "",
        min_interval_seconds: Optional[float] = None,
        retries: Optional[int] = None,
        default_days: Optional[float] = None,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.language = _clean(language or os.getenv("GDELT_SOURCE_LANG", "english")).casefold()
        self.min_interval_seconds = (
            float(min_interval_seconds)
            if min_interval_seconds is not None
            else _env_float("GDELT_MIN_INTERVAL_SECONDS", 6.0, 5.0, 120.0)
        )
        self.retries = int(retries if retries is not None else _env_float("GDELT_RETRIES", 2, 0, 5))
        self.default_days = float(default_days if default_days is not None else _env_float("GDELT_DEFAULT_DAYS", 30, 1, 90))
        self.transport = transport or _default_transport
        self.sleep = sleep
        self.clock = clock
        self.cooldown_seconds = _env_float("GDELT_COOLDOWN_SECONDS", 180.0, 10.0, 3600.0)
        # Total back-off allowed for one search after refusals. Default 12 s:
        # one retry after 10 s, then the cooldown (a refusal used to cost
        # about a minute: 10 s + 20 s back-off plus spacing).
        self.max_retry_wait_seconds = _env_float("GDELT_MAX_RETRY_WAIT_SECONDS", 12.0, 0.0, 120.0)
        # GDELT DOC supports searching back to 1 January 2017 (some modes and
        # window lengths are restricted; a refused window is a source error).
        self.coverage_note = "GDELT DOC 2.0 archive; result modes and window lengths are restricted"
        self.last_http_attempts = 0
        self.last_wait_seconds = 0.0

    @staticmethod
    def earliest_available() -> datetime:
        raw = _clean(os.getenv("GDELT_EARLIEST_DATE")) or "2017-01-01"
        return _parse_date(raw) or datetime(2017, 1, 1, tzinfo=timezone.utc)

    def _wait_turn(self) -> None:
        with GDELTDocProvider._lock:
            wait = GDELTDocProvider._last_request_at + self.min_interval_seconds - self.clock()
            if wait > 0:
                self.sleep(wait)
                self.last_wait_seconds += wait
            GDELTDocProvider._last_request_at = self.clock()

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        max_results: Optional[int] = None,
        timeout_seconds: float = 10.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        requested = max(1, min(int(max_results or limit or 10), 250))
        plain_terms = plain_query_terms(query)
        # A plain phrase is an OR search: fetch more and keep the headlines
        # that contain the most of its words.
        fetch = requested if plain_terms is None else min(250, max(requested * 10, 50))
        # max_retries=0 (the research step): one attempt, then the cooldown.
        # Breadth over persistence there; main discovery keeps the retries.
        retries = self.retries
        if kwargs.get("max_retries") is not None:
            try:
                retries = max(0, min(int(kwargs["max_retries"]), self.retries))
            except (TypeError, ValueError):
                pass
        self.last_http_attempts = 0
        self.last_wait_seconds = 0.0
        retry_wait = 0.0
        if self.clock() < GDELTDocProvider._cooldown_until:
            # Refused recently: do not hammer it; fail fast until the cooldown ends.
            raise FreeNewsRateLimitError(
                f"GDELT rate limit cooldown ({GDELTDocProvider._cooldown_until - self.clock():.0f} s left)"
            )
        gdelt_query = to_gdelt_query(query)
        if not gdelt_query:
            raise FreeNewsProviderError(f"query has no usable GDELT terms: {query!r}")
        if self.language:
            gdelt_query += f" sourcelang:{self.language}"
        start, end = _window(kwargs, self.default_days)
        params = {
            "query": gdelt_query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(fetch),
            "sort": "hybridrel",
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        }
        url = GDELT_DOC_ENDPOINT + "?" + urlencode(params)
        timeout = max(3.0, min(float(timeout_seconds or 10.0), 45.0))
        attempt = 0
        while True:
            self._wait_turn()
            self.last_http_attempts += 1
            request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"})
            try:
                status, body, _ = self.transport(request, timeout)
            except (URLError, TimeoutError, OSError) as error:
                raise FreeNewsProviderError(f"GDELT request failed: {type(error).__name__}: {error}") from error
            text = body.decode("utf-8", errors="replace").strip()
            # Only a non-JSON body can be a refusal message; headlines inside
            # JSON ("Too many AI pilots...") are results, not limits.
            plain_text = bool(text) and not text.startswith(("{", "["))
            limited = status == 429 or (
                plain_text and re.search(r"limit requests|rate limit|too many requests", text[:300], re.I)
            )
            if limited:
                backoff = 10.0 * (attempt + 1)
                if attempt >= retries or retry_wait + backoff > self.max_retry_wait_seconds:
                    GDELTDocProvider._cooldown_until = self.clock() + self.cooldown_seconds
                    raise FreeNewsRateLimitError(
                        f"GDELT rate limit (HTTP {status}) after {attempt + 1} attempts; "
                        f"pausing GDELT for {self.cooldown_seconds:.0f} s"
                    )
                attempt += 1
                self.sleep(backoff)
                self.last_wait_seconds += backoff
                retry_wait += backoff
                continue
            if status >= 400:
                raise FreeNewsProviderError(f"GDELT HTTP {status}: {text[:200]}")
            if not text:
                return self._payload(query, gdelt_query, [])  # GDELT returns empty body for no matches
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                # GDELT reports errors as HTTP 200 with a plain-text message.
                raise FreeNewsProviderError(f"GDELT error: {text[:200]}") from None
            break
        results = []
        seen: set[str] = set()
        for article in decoded.get("articles", []) if isinstance(decoded, Mapping) else []:
            if not isinstance(article, Mapping):
                continue
            link = _clean(article.get("url"))
            title = _clean(article.get("title"))
            if not link or not title or link in seen:
                continue
            seen.add(link)
            results.append(_result(
                provider=self.provider_name,
                url=link,
                title=title,
                published=_parse_date(article.get("seendate")),
                published_basis="gdelt_seen_date",
                source_name=_clean(article.get("domain")),
                extra={
                    "language": _clean(article.get("language")),
                    "source_country": _clean(article.get("sourcecountry")),
                },
            ))
        if plain_terms is not None:
            # OR search returned headlines with any word: keep those that
            # match the phrase, best first.
            scored = [
                (query_match_score(query, row["title"]), position, row)
                for position, row in enumerate(results)
                if query_matches(query, row["title"])
            ]
            results = []
            for score, _, row in sorted(scored, key=lambda item: (-item[0], item[1])):
                row["query_match_share"] = round(score, 3)
                results.append(row)
        return self._payload(query, gdelt_query, results[:requested])

    def _payload(self, query: str, provider_query: str, results: list) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "query": query,
            "provider_query": provider_query,
            "results": results,
            "result_count": len(results),
        }


# ----------------------------------------------------------------------
# RSS / Atom feeds
# ----------------------------------------------------------------------

DEFAULT_FEED_FILE = Path("config") / "news_feeds.txt"


def load_feed_list(path: Optional[Path] = None) -> list[str]:
    configured = _clean(os.getenv("NEWS_RSS_FEEDS"))
    if configured:
        return [url.strip() for url in configured.split(",") if url.strip()]
    candidates = [path] if path else [
        Path(os.getenv("NEWS_RSS_FEED_FILE", "")) if os.getenv("NEWS_RSS_FEED_FILE") else None,
        Path.cwd() / DEFAULT_FEED_FILE,
        Path(__file__).resolve().parent.parent / DEFAULT_FEED_FILE,
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            lines = candidate.read_text(encoding="utf-8").splitlines()
            urls = []
            for line in lines:
                parts = line.split("#", 1)[0].split()
                if parts:
                    urls.append(parts[0])
            return urls
    return []


def _simple_parse_feed(raw: bytes) -> list[dict[str, Any]]:
    """Minimal RSS 2.0 / Atom parser (used when services.rss_service is missing)."""
    import xml.etree.ElementTree as ET

    items: list[dict[str, Any]] = []
    root = ET.fromstring(raw)

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].casefold()

    for node in root.iter():
        if local(node.tag) not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        for child in node:
            name = local(child.tag)
            if name == "link" and child.get("href"):
                fields.setdefault("link", child.get("href") or "")
            elif child.text:
                fields.setdefault(name, child.text)
        items.append({
            "url": _clean(fields.get("link")),
            "title": _clean(fields.get("title")),
            "summary": re.sub(r"<[^>]+>", " ", fields.get("description") or fields.get("summary") or ""),
            "published_at": _clean(fields.get("pubdate") or fields.get("published") or fields.get("updated")),
        })
    return items


class RSSFeedProvider:
    """Reads the configured feeds (cached RSS_CACHE_SECONDS, default 900) and
    returns items matching the query (every AND-group must match) inside the
    date window. Explicit date bounds default to strict dates: undated and
    out-of-window items are excluded. Live unbounded searches can use clock
    tolerance; every retained item's date status is explicit. A repeatedly
    failing feed is paused until its cooldown expires."""

    provider_name = "rss"

    def __init__(
        self,
        *,
        feeds: Optional[Sequence[str]] = None,
        cache_seconds: Optional[float] = None,
        default_days: Optional[float] = None,
        fetcher: Optional[Callable[[str, float], list[dict[str, Any]]]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.feeds = list(feeds) if feeds is not None else load_feed_list()
        if not self.feeds:
            raise FreeNewsConfigurationError(
                "No RSS feeds configured. Add feed URLs to config/news_feeds.txt or NEWS_RSS_FEEDS."
            )
        self.cache_seconds = float(cache_seconds if cache_seconds is not None else _env_float("RSS_CACHE_SECONDS", 900, 0, 86400))
        self.default_days = float(default_days if default_days is not None else _env_float("RSS_DEFAULT_DAYS", 30, 1, 365))
        self.max_failures = int(_env_float("RSS_MAX_FEED_FAILURES", 3, 1, 20))
        self.feed_cooldown_seconds = _env_float("RSS_FEED_COOLDOWN_SECONDS", 1800, 60, 86400)
        # Publisher clocks and ambiguous zone names ("IST") can put items hours
        # "in the future" or past the window edge; allow this much slack.
        self.date_tolerance = timedelta(hours=_env_float("RSS_DATE_TOLERANCE_HOURS", 14, 0, 72))

        self.fetcher = fetcher or self._fetch_with_project_service
        self.clock = clock
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self.feed_status: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _fetch_with_project_service(feed_url: str, timeout: float) -> list[dict[str, Any]]:
        try:
            from services.rss_service import RSSService  # the project's own RSS reader
        except Exception:
            RSSService = None  # type: ignore[assignment]
        if RSSService is not None:
            parsed = RSSService(timeout_seconds=timeout).fetch_feed(feed_url)
            return list(parsed.get("items") or []) if isinstance(parsed, Mapping) else []
        request = Request(feed_url, headers={"User-Agent": DEFAULT_USER_AGENT})
        status, body, _ = _default_transport(request, timeout)
        if status >= 400:
            raise FreeNewsProviderError(f"feed HTTP {status}")
        return _simple_parse_feed(body)

    def _items(self, feed_url: str, timeout: float) -> list[dict[str, Any]]:
        now = self.clock()
        with self._lock:
            cached = self._cache.get(feed_url)
            status = self.feed_status.setdefault(
                feed_url, {"failures": 0, "last_error": "", "items": 0, "paused_until": 0.0}
            )
            if cached and now - cached[0] < self.cache_seconds:
                return cached[1]
            if status["failures"] >= self.max_failures and now < status.get("paused_until", 0.0):
                raise FreeNewsRateLimitError(
                    f"feed paused for {status['paused_until'] - now:.0f} s after {status['failures']} failures"
                )
        try:
            items = self.fetcher(feed_url, timeout)
        except Exception as error:
            with self._lock:
                status["failures"] += 1
                status["last_error"] = f"{type(error).__name__}: {str(error)[:160]}"
                if status["failures"] >= self.max_failures:
                    # Pause, then try again later (a network blip must not
                    # switch a feed off for good).
                    status["paused_until"] = now + self.feed_cooldown_seconds
            raise
        with self._lock:
            status.update({"failures": 0, "last_error": "", "items": len(items)})
            self._cache[feed_url] = (now, items)
        return items

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        max_results: Optional[int] = None,
        timeout_seconds: float = 10.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        requested = max(1, min(int(max_results or limit or 10), 100))
        start, end = _window(kwargs, self.default_days)
        bounded = any(_clean(kwargs.get(key)) for key in ("from_date", "from", "to_date", "to"))
        raw_strict = kwargs.get("strict_dates", bounded)
        if isinstance(raw_strict, bool):
            strict_dates = raw_strict
        elif str(raw_strict).strip().casefold() in {"true", "1", "false", "0"}:
            strict_dates = str(raw_strict).strip().casefold() in {"true", "1"}
        else:
            raise ValueError("strict_dates must be true or false")
        for key in ("from_date", "from", "to_date", "to"):
            if _clean(kwargs.get(key)) and _parse_date(kwargs[key]) is None:
                raise ValueError(f"Invalid RSS date bound: {key}")
        if start > end:
            raise ValueError("RSS date window starts after it ends")
        date_rejections = {"unknown_date": 0, "outside_window": 0}
        timeout = max(3.0, min(float(timeout_seconds or 10.0), 30.0))
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        fetched, failed, paused = 0, [], 0
        oldest_exposed: Optional[datetime] = None
        for feed_url in self.feeds:
            try:
                feed_items = self._items(feed_url, timeout)
            except Exception as error:
                failed.append(f"{feed_url}: {type(error).__name__}")
                paused += isinstance(error, FreeNewsRateLimitError)
                continue
            fetched += 1
            for item in feed_items:
                if not isinstance(item, Mapping):
                    continue
                link = _clean(item.get("url") or item.get("link") or item.get("canonical_url"))
                title = _clean(item.get("title"))
                summary = re.sub(r"<[^>]+>", " ", str(item.get("summary") or item.get("description") or ""))
                if not link or not title or link in seen:
                    continue
                published = _parse_date(item.get("published_at") or item.get("published") or item.get("pubDate"))
                if published is not None and (oldest_exposed is None or published < oldest_exposed):
                    oldest_exposed = published
                if strict_dates and published is None:
                    date_rejections["unknown_date"] += 1
                    continue
                tolerance = timedelta(0) if strict_dates else self.date_tolerance
                if published and not (start - tolerance <= published <= end + tolerance):
                    date_rejections["outside_window"] += 1
                    continue
                date_status = (
                    "unknown" if published is None else
                    ("within_window" if start <= published <= end else "outside_window_tolerated")
                )
                if not query_matches(query, f"{title} {summary}"):
                    continue
                seen.add(link)
                results.append(_result(
                    provider=self.provider_name, url=link, title=title, snippet=summary,
                    published=published, source_name=_domain(feed_url),
                    extra={"feed_url": feed_url, "date_window_status": date_status},
                ))
        if failed and not fetched:
            if paused == len(failed):
                # Every feed is in its cooldown: nothing new can come from RSS
                # for a while, so report it like a rate limit.
                raise FreeNewsRateLimitError(
                    f"all {len(failed)} RSS feeds are paused after repeated failures (rate limit cooldown)"
                )
            raise FreeNewsProviderError(f"all RSS feeds failed or are paused: {'; '.join(failed[:5])}")
        if bounded and fetched and oldest_exposed is not None and end < oldest_exposed:
            # Feeds only expose their latest items. A window that ends before
            # the oldest item they show cannot be searched here at all.
            raise FreeNewsOutOfCoverageError(
                f"RSS feeds currently expose items from {_iso(oldest_exposed)}; "
                f"requested window ends {_iso(end)}"
            )
        if plain_query_terms(query) is not None:
            # Plain research phrase: most matching words first, then newest.
            for row in results:
                row["query_match_share"] = round(
                    query_match_score(query, f"{row.get('title', '')} {row.get('snippet', '')}"), 3
                )
            results.sort(key=lambda row: (row["query_match_share"], row.get("published_at") or ""), reverse=True)
        else:
            results.sort(key=lambda row: row.get("published_at") or "", reverse=True)
        return {
            "provider": self.provider_name,
            "query": query,
            "results": results[:requested],
            "result_count": min(len(results), requested),
            "matched_total": len(results),
            "date_filter": {
                "strict": strict_dates, "start": _iso(start), "end": _iso(end),
                "rejected": date_rejections,
            },
            "feed_status": {url: dict(status) for url, status in self.feed_status.items()},
        }


# ----------------------------------------------------------------------
# Exa
# ----------------------------------------------------------------------

class ExaProvider:
    """Exa /search (category "news" by default). Needs EXA_API_KEY. Requests no
    page contents, so each search costs only the base search price; usage
    counts against Exa's free monthly credit. EXA_MAX_REQUESTS_PER_PROCESS
    (default 200) caps spending from one app session."""

    # Web search with publication-date filters; its archive depth is not
    # guaranteed, so it is always asked and never marked out of coverage.
    coverage_note = "web search; date coverage not guaranteed"

    provider_name = "exa"

    def __init__(
        self,
        *,
        api_key: str = "",
        category: str = "",
        default_days: Optional[float] = None,
        max_requests: Optional[int] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        self.api_key = _clean(api_key or os.getenv("EXA_API_KEY"))
        if not self.api_key:
            raise FreeNewsConfigurationError("EXA_API_KEY is not set.")
        self.category = _clean(category or os.getenv("EXA_CATEGORY", "news"))
        self.default_days = float(default_days if default_days is not None else _env_float("EXA_DEFAULT_DAYS", 30, 1, 3650))
        self.max_requests = int(max_requests if max_requests is not None else _env_float("EXA_MAX_REQUESTS_PER_PROCESS", 200, 1, 10000))
        self.requests_made = 0
        self.transport = transport or _default_transport

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        max_results: Optional[int] = None,
        timeout_seconds: float = 10.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if self.requests_made >= self.max_requests:
            raise FreeNewsRateLimitError(
                f"Exa request cap for this session reached ({self.max_requests}); raise EXA_MAX_REQUESTS_PER_PROCESS to allow more."
            )
        requested = max(1, min(int(max_results or limit or 10), 25))
        natural = _clean(kwargs.get("natural_query")) or to_natural_query(query) or _clean(query)
        start, end = _window(kwargs, self.default_days)
        body: dict[str, Any] = {
            "query": natural,
            "type": _clean(kwargs.get("exa_type")) or "auto",
            "numResults": requested,
            "startPublishedDate": _iso(start),
            "endPublishedDate": _iso(end),
        }
        category = _clean(kwargs.get("exa_category")) if "exa_category" in kwargs else self.category
        if category:
            body["category"] = category
        request = Request(
            EXA_SEARCH_ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={"x-api-key": self.api_key, "Content-Type": "application/json", "User-Agent": DEFAULT_USER_AGENT},
            method="POST",
        )
        timeout = max(3.0, min(float(timeout_seconds or 10.0), 45.0))
        self.requests_made += 1
        try:
            status, raw, _ = self.transport(request, timeout)
        except (URLError, TimeoutError, OSError) as error:
            raise FreeNewsProviderError(f"Exa request failed: {type(error).__name__}: {error}") from error
        text = raw.decode("utf-8", errors="replace")
        if status == 429:
            raise FreeNewsRateLimitError("Exa rate limit or credit exhausted (HTTP 429)")
        if status in {401, 402, 403}:
            raise FreeNewsConfigurationError(f"Exa refused the request (HTTP {status}); check EXA_API_KEY and credit")
        if status >= 400:
            raise FreeNewsProviderError(f"Exa HTTP {status}: {text[:200]}")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as error:
            raise FreeNewsProviderError("Exa returned invalid JSON") from error
        results = []
        for row in decoded.get("results", []) if isinstance(decoded, Mapping) else []:
            if not isinstance(row, Mapping):
                continue
            link = _clean(row.get("url"))
            title = _clean(row.get("title"))
            if not link or not title:
                continue
            results.append(_result(
                provider=self.provider_name, url=link, title=title,
                published=_parse_date(row.get("publishedDate")),
                source_name=_domain(link),
                extra={"author": _clean(row.get("author")), "exa_id": _clean(row.get("id"))},
            ))
        return {
            "provider": self.provider_name,
            "query": query,
            "provider_query": natural,
            "results": results,
            "result_count": len(results),
            "cost_estimate_usd": float(decoded.get("costDollars", {}).get("total", 0.0))
            if isinstance(decoded.get("costDollars"), Mapping) else None,
        }


__all__ = [
    "SERVICE_VERSION",
    "FreeNewsProviderError",
    "FreeNewsRateLimitError",
    "FreeNewsConfigurationError",
    "FreeNewsOutOfCoverageError",
    "GDELTDocProvider",
    "RSSFeedProvider",
    "ExaProvider",
    "load_feed_list",
    "parse_query_groups",
    "plain_query_terms",
    "query_match_score",
    "query_matches",
    "to_gdelt_query",
    "to_natural_query",
]
