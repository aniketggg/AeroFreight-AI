"""Step 4 — Browser-based form retrieval (Owner: Aniket).

This is the "automated tool" half of the spec: it *searches* for the latest
required CBP forms and Air Waybill / Bill of Lading templates and returns their
blank JSON/text structures. Two modes, selected per-call or via env var:

  * **Simulated** (default): no network. Returns the curated blank skeleton
    from the form catalog and logs the canonical source URL it "fetched" from.
    This keeps the agent, demo, and tests fully offline and deterministic.

  * **Live** (``AEROFREIGHT_COMPLIANCE_LIVE=true`` or ``live=True``): performs a
    real web search to confirm the current official source URL for the form —
    via the Tavily Search API when ``TAVILY_API_KEY`` is set, otherwise a plain
    ``httpx`` reachability check against the catalog URL (the WebBaseLoader-style
    fallback). The blank *structure* itself is always taken from the curated
    skeleton — real government forms are PDFs, so the field skeleton is what
    downstream agents actually fill; live mode refreshes/verifies provenance.

Any live failure (no key, timeout, network down) degrades gracefully to the
simulated path, so a missing API key never breaks the pipeline.

The retrieval functions duck-type the form object: they only read ``.name``,
``.source_url`` and ``.blank_structure``, so there is no import dependency back
on :mod:`compliance_agent.compliance` (no circular import).
"""

from __future__ import annotations

import copy
import os
from typing import Optional

# Tavily search endpoint (used only in live mode when an API key is present).
_TAVILY_URL = "https://api.tavily.com/search"
_HTTP_TIMEOUT_S = 8.0


def live_enabled() -> bool:
    """Default live/simulated choice from the environment."""
    return os.getenv("AEROFREIGHT_COMPLIANCE_LIVE", "false").lower() == "true"


def _log(logger, level: str, message: str) -> None:
    """Log via a uagents ctx.logger if given, else stay silent."""
    if logger is not None:
        getattr(logger, level, lambda *_a, **_k: None)(message)


# --------------------------------------------------------------------------- #
# Live search backends (best-effort; never raise to the caller)
# --------------------------------------------------------------------------- #
def _tavily_top_url(query: str, api_key: str, logger=None) -> Optional[str]:
    """Return the top result URL from a Tavily search, or None on any failure."""
    try:
        import httpx

        resp = httpx.post(
            _TAVILY_URL,
            json={"api_key": api_key, "query": query, "max_results": 1},
            timeout=_HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if results:
            return results[0].get("url")
    except Exception as exc:  # noqa: BLE001 — any failure -> fall back to curated
        _log(logger, "warning", f"[retrieval] Tavily search failed: {exc!r}")
    return None


def _url_reachable(url: str, logger=None) -> bool:
    """WebBaseLoader-style check: is the curated source URL actually live?"""
    try:
        import httpx

        resp = httpx.head(url, timeout=_HTTP_TIMEOUT_S, follow_redirects=True)
        return resp.status_code < 400
    except Exception as exc:  # noqa: BLE001
        _log(logger, "warning", f"[retrieval] reachability check failed: {exc!r}")
        return False


def search_form_source(
    form_name: str, fallback_url: str, *, live: bool, logger=None
) -> str:
    """Resolve the source URL we 'retrieved' a form template from.

    Simulated mode returns the curated catalog URL. Live mode tries Tavily
    (if ``TAVILY_API_KEY`` is set), then a reachability check, and falls back to
    the curated URL if neither confirms a better one.
    """
    if not live:
        return fallback_url

    query = f"{form_name} official blank form template filing instructions"
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if api_key:
        found = _tavily_top_url(query, api_key, logger=logger)
        if found:
            return found
    # No key (or Tavily failed): verify the curated URL is still reachable.
    _url_reachable(fallback_url, logger=logger)
    return fallback_url


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def retrieve_blank_form(form, *, live: Optional[bool] = None, logger=None) -> dict:
    """Fetch the blank JSON structure for one form.

    ``form`` is any object exposing ``.name``, ``.source_url`` and
    ``.blank_structure`` (a :class:`compliance_agent.compliance.FormSpec`).
    Returns a deep copy of the skeleton so callers can mutate it freely without
    corrupting the shared catalog.
    """
    live = live_enabled() if live is None else live
    url = search_form_source(form.name, form.source_url, live=live, logger=logger)
    mode = "LIVE" if live else "SIM"
    _log(logger, "info", f"[retrieval:{mode}] fetched blank '{form.name}' from {url}")
    return copy.deepcopy(form.blank_structure)
