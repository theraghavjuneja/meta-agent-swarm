"""Deterministic scoping rules for the research harness. Pure functions, no I/O.

The model decides *what* to look for; this module decides what is *allowed* to count.
Every rule here is industry-agnostic: it knows about kinds of websites and kinds of
questions, never about perfume, protein or any other vertical.

Three gates, each producing a reason string that goes into the trace:

1. **Query gate** (`check_query`): a search must mention at least one of the plan's
   anchor terms (category / buyer vocabulary / audience) and must not be a
   background-knowledge query ("how is X made", "history of X", "what is X",
   ingredients/manufacturing). Those are the queries that sent earlier runs off
   into encyclopedic tangents.
2. **Result gate** (`screen_results`): results from source types that cannot tell us
   anything about an audience -- general mass-news outlets, encyclopedias, video/social
   platforms whose pages cannot be read -- are dropped before the model ever sees them,
   as are results whose title/snippet mention none of the anchor terms.
3. **Read gate** (enforced in the loop): only URLs that survived the result gate may be
   read, and at most N per domain, so the agent cannot wander to URLs it invented or
   that a page told it to visit, and cannot build "3 sources" out of one site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from app.research.dto import SourceType
from app.research.ports import SearchResult

__all__ = [
    "DEFAULT_BLOCKED_TYPES",
    "ScreenedResult",
    "blocked_domains",
    "check_query",
    "classify_source",
    "normalise_anchor_terms",
    "registrable_domain",
    "screen_results",
]

# --------------------------------------------------------------------------------------
# Domain knowledge (generic across verticals)
# --------------------------------------------------------------------------------------

# General-audience news: fine for current events, useless for "how does this audience
# talk about this need" -- and the single biggest source of off-topic reads.
_NEWS_DOMAINS = {
    "timesofindia.indiatimes.com", "indiatimes.com", "economictimes.indiatimes.com",
    "ndtv.com", "hindustantimes.com", "indianexpress.com", "news18.com", "thehindu.com",
    "livemint.com", "business-standard.com", "deccanherald.com", "firstpost.com",
    "zeenews.india.com", "indiatoday.in", "moneycontrol.com", "financialexpress.com",
    "cnn.com", "foxnews.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com", "bbc.com",
    "bbc.co.uk", "nytimes.com", "washingtonpost.com", "theguardian.com", "usatoday.com",
    "dailymail.co.uk", "nypost.com", "reuters.com", "apnews.com", "bloomberg.com",
    "cnbc.com", "independent.co.uk", "mirror.co.uk", "express.co.uk", "thesun.co.uk",
    "telegraph.co.uk", "huffpost.com", "latimes.com", "wsj.com", "npr.org", "msn.com",
    "news.yahoo.com", "aljazeera.com", "scmp.com", "straitstimes.com", "abc.net.au",
    "smh.com.au", "cbc.ca", "globalnews.ca", "news.com.au", "time.com", "newsweek.com",
}
_REFERENCE_DOMAINS = {
    "wikipedia.org", "britannica.com", "wikihow.com", "dictionary.com",
    "merriam-webster.com", "investopedia.com", "howstuffworks.com", "fandom.com",
}
_SOCIAL_VIDEO_DOMAINS = {
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com",
    "pinterest.com", "x.com", "twitter.com", "linkedin.com", "threads.net", "vimeo.com",
}
_COMMUNITY_DOMAINS = {
    "reddit.com", "quora.com", "stackexchange.com", "discord.com", "mumsnet.com",
    "discourse.org", "producthunt.com", "boards.ie",
}
_REVIEW_DOMAINS = {
    "trustpilot.com", "yelp.com", "g2.com", "capterra.com", "sitejabber.com",
    "consumerreports.org", "which.co.uk", "influenster.com", "reviews.io",
}
_RETAIL_DOMAINS = {
    "amazon.com", "amazon.in", "amazon.co.uk", "flipkart.com", "walmart.com",
    "target.com", "ebay.com", "etsy.com", "nykaa.com", "sephora.com", "ulta.com",
    "bestbuy.com", "costco.com", "iherb.com", "myntra.com", "ajio.com",
}
_INDUSTRY_DOMAINS = {
    "mckinsey.com", "nielsen.com", "nielseniq.com", "euromonitor.com", "mintel.com",
    "statista.com", "deloitte.com", "bcg.com", "bain.com", "kantar.com", "ipsos.com",
    "yougov.com", "gwi.com", "thinkwithgoogle.com", "warc.com", "adage.com",
    "adweek.com", "marketingweek.com", "campaignlive.com", "campaignlive.co.uk",
    "thedrum.com", "businessoffashion.com", "voguebusiness.com", "glossy.co",
    "modernretail.co", "retaildive.com", "fooddive.com", "cosmeticsdesign.com",
    "nutraingredients.com", "foodnavigator.com", "bevnet.com", "morningconsult.com",
    "pewresearch.org", "hbr.org", "emarketer.com", "insiderintelligence.com",
    "similarweb.com", "semrush.com", "hubspot.com", "shopify.com",
}
_RESEARCH_DOMAINS = {
    "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "sciencedirect.com", "springer.com",
    "nature.com", "researchgate.net", "frontiersin.org", "mdpi.com", "jstor.org",
    "scholar.google.com", "arxiv.org", "wiley.com", "tandfonline.com",
}

DEFAULT_BLOCKED_TYPES: frozenset[SourceType] = frozenset(
    {SourceType.NEWS, SourceType.REFERENCE, SourceType.SOCIAL_VIDEO}
)

# Background-knowledge phrasings. Each is a query shape that answers "what is this
# thing" rather than "what does this audience think, want or do" -- in any vertical.
_BACKGROUND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), label)
    for p, label in (
        (r"\bhow (?:is|are|do|does)\b.*\b(?:made|manufactured|produced|work|works)\b", "how-it-is-made/how-it-works"),
        (r"\bhistory of\b|\borigins? of\b|\binvented\b|\binvention of\b", "history"),
        # Short definitional questions only ("what is whey protein"); longer audience
        # questions ("what are runners' biggest complaints about ...") are fine.
        (r"^\s*what (?:is|are) (?:an? |the )?[\w\-' ]{1,30}\??\s*$", "definition"),
        (r"\b(?:definition|meaning) of\b|\bdefine\b", "definition"),
        (r"\bmanufactur\w*|\bproduction process\b|\bsupply chain\b", "manufacturing"),
        (r"\bingredients? (?:of|in|list)\b|\bchemical composition\b|\bchemistry of\b", "composition"),
        (r"\bstock price\b|\bshare price\b|\bquarterly results\b|\bearnings\b|\bacquisition\b|\bceo\b", "company/financial news"),
        (r"\bwikipedia\b", "encyclopedia"),
    )
)

_STOPWORDS = {
    "a", "an", "and", "or", "the", "of", "for", "to", "in", "on", "with", "by", "at",
    "is", "are", "be", "my", "your", "our", "their", "who", "what", "how", "why",
    "best", "top", "vs", "new", "people", "users", "customers",
}


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def registrable_domain(url: str) -> str:
    """Approximate eTLD+1 (good enough for a per-domain read cap)."""
    parts = _host(url).split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def classify_source(url: str) -> SourceType:
    host = _host(url)
    path = urlparse(url).path.lower()
    if _matches(host, _SOCIAL_VIDEO_DOMAINS):
        return SourceType.SOCIAL_VIDEO
    if _matches(host, _REFERENCE_DOMAINS):
        return SourceType.REFERENCE
    if _matches(host, _NEWS_DOMAINS) or host.startswith("news."):
        return SourceType.NEWS
    if _matches(host, _COMMUNITY_DOMAINS) or re.search(r"(^|\.)(forums?|community|boards|discuss)\.", host + ".") or "/forum" in path or "/community/" in path or "/threads/" in path:
        return SourceType.COMMUNITY
    if _matches(host, _REVIEW_DOMAINS) or "/reviews" in path or "/review/" in path:
        return SourceType.REVIEWS
    if _matches(host, _RESEARCH_DOMAINS) or host.endswith(".edu") or host.endswith(".gov") or ".ac." in host:
        return SourceType.RESEARCH
    if _matches(host, _INDUSTRY_DOMAINS):
        return SourceType.INDUSTRY
    if _matches(host, _RETAIL_DOMAINS) or "/products/" in path or "/product/" in path:
        return SourceType.RETAIL_OR_BRAND
    return SourceType.EDITORIAL


def blocked_domains(blocked_types: Iterable[SourceType], extra: Iterable[str] = ()) -> list[str]:
    """Domains to pass to the search provider's exclude list for the blocked types."""
    by_type = {
        SourceType.NEWS: _NEWS_DOMAINS,
        SourceType.REFERENCE: _REFERENCE_DOMAINS,
        SourceType.SOCIAL_VIDEO: _SOCIAL_VIDEO_DOMAINS,
    }
    domains: set[str] = {d.strip().lower() for d in extra if d.strip()}
    for source_type in blocked_types:
        domains |= by_type.get(source_type, set())
    return sorted(domains)


# --------------------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9\-']*", text.lower()) if t not in _STOPWORDS}


def normalise_anchor_terms(terms: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for term in terms:
        t = " ".join(term.lower().split())
        if t and t not in cleaned:
            cleaned.append(t)
    return cleaned


def _mentions_anchor(text: str, anchors: Sequence[str]) -> bool:
    """True if any anchor phrase appears, or all of a multi-word anchor's words do.

    Singular/plural are folded ("shoe" matches "shoes") so phrasing is not penalised.
    """
    lowered = text.lower()
    words = {w.rstrip("s") for w in _tokens(lowered)}
    for anchor in anchors:
        if anchor in lowered:
            return True
        anchor_words = {w.rstrip("s") for w in _tokens(anchor)}
        if anchor_words and anchor_words <= words:
            return True
    return False


def check_query(query: str, anchors: Sequence[str]) -> str | None:
    """Return a rejection reason, or None if the query is in scope."""
    for pattern, label in _BACKGROUND_PATTERNS:
        if pattern.search(query):
            return (
                f"background-knowledge query ({label}); it cannot tell us what the "
                "audience thinks, wants or does"
            )
    if anchors and not _mentions_anchor(query, anchors):
        return (
            "query mentions none of the plan's anchor terms "
            f"({', '.join(anchors[:6])}), so results would not be about this category "
            "or audience"
        )
    return None


@dataclass(frozen=True, slots=True)
class ScreenedResult:
    result: SearchResult
    source_type: SourceType
    kept: bool
    reason: str | None = None


def screen_results(
    results: Sequence[SearchResult],
    *,
    anchors: Sequence[str],
    blocked_types: Iterable[SourceType],
    extra_blocked_domains: Iterable[str] = (),
) -> list[ScreenedResult]:
    """Label every result; keep the in-scope ones, ranked best first."""
    blocked = set(blocked_types)
    extra = [d.lower() for d in extra_blocked_domains]
    screened: list[ScreenedResult] = []
    for r in results:
        source_type = classify_source(r.url)
        reason = None
        if source_type in blocked:
            reason = f"source type '{source_type.value}' is out of scope for audience research"
        elif extra and _matches(_host(r.url), extra):
            reason = "domain is on the configured block list"
        elif anchors and not _mentions_anchor(f"{r.title} {r.snippet}", anchors):
            reason = "title/snippet mention none of the plan's anchor terms (off-topic)"
        screened.append(ScreenedResult(result=r, source_type=source_type, kept=reason is None, reason=reason))
    kept = sorted((s for s in screened if s.kept), key=lambda s: -(s.result.score or 0.0))
    return kept + [s for s in screened if not s.kept]
