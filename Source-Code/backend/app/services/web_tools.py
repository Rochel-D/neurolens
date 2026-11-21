"""External knowledge tools: PubMed research, MedlinePlus health topics, Serper web search.

PubMed and MedlinePlus are reached through the official MCP *fetch* server (mcp-server-fetch,
stdio transport) when USE_MCP=true. We do not build a custom MCP server. If MCP cannot start,
the same URLs are fetched directly with httpx, so the app never depends on MCP being up.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx

from ..config import get_settings
from . import mcp_client

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MEDLINEPLUS_WS = "https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&retmax=3&term="


def _fetch(url: str, trace: list | None = None) -> str:
    """Fetch a URL via the MCP fetch server, falling back to httpx."""
    s = get_settings()
    if s.use_mcp:
        try:
            text = mcp_client.fetch(url, timeout=s.mcp_timeout_s)
            if trace is not None:
                trace.append({"via": "mcp:fetch", "url": url[:90]})
            return text
        except Exception as e:  # noqa: BLE001 — any MCP failure → direct fetch
            if trace is not None:
                trace.append({"via": "mcp:fetch", "error": type(e).__name__})
    r = httpx.get(url, timeout=15, headers={"User-Agent": "NeuroLens-student-project/1.0"})
    r.raise_for_status()
    if trace is not None:
        trace.append({"via": "httpx", "url": url[:90]})
    return r.text


def _json_from(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def search_pubmed(query: str, k: int = 4, trace: list | None = None) -> list[dict]:
    """Recent, relevant PubMed papers: title, journal, year, link."""
    q = quote_plus(query)
    ids = _json_from(_fetch(f"{EUTILS}/esearch.fcgi?db=pubmed&retmode=json&sort=relevance&retmax={k}&term={q}", trace))
    pmids = ids.get("esearchresult", {}).get("idlist", [])[:k]
    if not pmids:
        return []
    summ = _json_from(_fetch(f"{EUTILS}/esummary.fcgi?db=pubmed&retmode=json&id={','.join(pmids)}", trace))
    out = []
    for pid in pmids:
        d = summ.get("result", {}).get(pid, {})
        if not d:
            continue
        out.append(
            {
                "title": d.get("title", "").rstrip("."),
                "journal": d.get("fulljournalname") or d.get("source", ""),
                "year": (d.get("pubdate") or "")[:4],
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                "source": "PubMed",
            }
        )
    return out


def search_medlineplus(term: str, trace: list | None = None) -> list[dict]:
    """MedlinePlus health-topic search (NLM web service, XML)."""
    raw = _fetch(MEDLINEPLUS_WS + quote_plus(term), trace)
    start = raw.find("<nlmSearchResult")
    if start < 0:
        return []
    root = ET.fromstring(raw[start:])
    out = []
    for doc in root.iter("document"):
        fields = {c.get("name"): re.sub(r"<[^>]+>", "", "".join(c.itertext())) for c in doc.iter("content")}
        out.append(
            {
                "title": fields.get("title", "").strip(),
                "snippet": (fields.get("FullSummary") or fields.get("snippet") or "")[:500].strip(),
                "url": doc.get("url", ""),
                "source": "MedlinePlus",
            }
        )
    return out


def serper_search(query: str, k: int = 4, trace: list | None = None) -> list[dict]:
    key = get_settings().serper_api_key
    if not key:
        return []
    r = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": k, "gl": "in"},
        timeout=12,
    )
    r.raise_for_status()
    if trace is not None:
        trace.append({"via": "serper", "q": query[:60]})
    return [
        {"title": o.get("title", ""), "snippet": o.get("snippet", ""), "url": o.get("link", ""), "source": "Web (Serper)"}
        for o in r.json().get("organic", [])[:k]
    ]
