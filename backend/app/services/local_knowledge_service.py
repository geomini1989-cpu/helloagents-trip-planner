"""Official/local attraction knowledge retrieval and deterministic claim verification.

AMap remains responsible for POI/location/route data. Operational facts such as opening
hours, reservation rules, closure notices and admission policies are retrieved from an
independent web-search provider and then summarized by a dedicated Local Knowledge Agent.

The Agent is not trusted to invent citations. Any claim marked as verified is accepted as
source-backed only when its ``source_url`` exactly matches a URL returned by the current
search request. Unsupported citations are retained for evaluation but are never forwarded
to Planner/Revision as verified facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import get_settings


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_ALLOWED_CLAIM_TYPES = {
    "opening_hours",
    "reservation",
    "closure",
    "ticket",
    "admission",
    "temporary_notice",
    "other",
}
_ALLOWED_VERIFICATION_STATUS = {"verified", "unverified", "unsupported"}


@dataclass
class LocalKnowledgeSource:
    title: str
    url: str
    content: str
    score: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "score": self.score,
        }


@dataclass
class LocalKnowledgeClaim:
    attraction: str
    claim_type: str
    claim: str
    verification_status: str
    source_url: Optional[str] = None
    source_title: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attraction": self.attraction,
            "claim_type": self.claim_type,
            "claim": self.claim,
            "verification_status": self.verification_status,
            "source_url": self.source_url,
            "source_title": self.source_title,
        }


@dataclass
class LocalKnowledgeResult:
    query: str
    sources: List[LocalKnowledgeSource] = field(default_factory=list)
    claims: List[LocalKnowledgeClaim] = field(default_factory=list)
    provider: str = "tavily"
    degraded: bool = False
    error: Optional[str] = None
    claim_parse_error: Optional[str] = None

    @property
    def supported_claims(self) -> List[LocalKnowledgeClaim]:
        return [item for item in self.claims if item.verification_status == "verified"]

    @property
    def unsupported_claims(self) -> List[LocalKnowledgeClaim]:
        return [item for item in self.claims if item.verification_status == "unsupported"]

    @property
    def unverified_claims(self) -> List[LocalKnowledgeClaim]:
        return [item for item in self.claims if item.verification_status == "unverified"]

    def claim_metrics(self) -> Dict[str, Any]:
        total = len(self.claims)
        supported = len(self.supported_claims)
        unsupported = len(self.unsupported_claims)
        unverified = len(self.unverified_claims)
        return {
            "total_claims": total,
            "supported_claims": supported,
            "unsupported_claims": unsupported,
            "unverified_claims": unverified,
            "supported_claim_rate": round(supported / total, 4) if total else 0.0,
            "unsupported_claim_rate": round(unsupported / total, 4) if total else 0.0,
            "unverified_claim_rate": round(unverified / total, 4) if total else 0.0,
            "source_count": len(self.sources),
            "claim_parse_error": self.claim_parse_error,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "provider": self.provider,
            "degraded": self.degraded,
            "error": self.error,
            "claim_parse_error": self.claim_parse_error,
            "sources": [source.to_dict() for source in self.sources],
            "claims": [claim.to_dict() for claim in self.claims],
            "claim_metrics": self.claim_metrics(),
        }

    def as_agent_context(self) -> str:
        if self.degraded:
            return (
                "官方/本地知识检索当前不可用。不要猜测开放时间、预约、闭馆日、门票或临时公告。"
                f"原因：{self.error or 'unknown'}"
            )
        if not self.sources:
            return "没有检索到足够可靠的官方/本地知识来源；不要自行补造规则。"
        lines = ["以下是 Web Search 检索到的候选来源。只能引用下列 URL："]
        for index, source in enumerate(self.sources, start=1):
            lines.append(
                f"[{index}] {source.title}\nURL: {source.url}\n摘要: {source.content[:900]}"
            )
        return "\n\n".join(lines)

    def as_planner_context(self) -> str:
        """Return only deterministically supported facts plus explicit uncertainty."""
        payload = {
            "verified_claims": [item.to_dict() for item in self.supported_claims],
            "unverified_claims": [item.to_dict() for item in self.unverified_claims],
            "safety_note": (
                "Only verified_claims may be treated as operational facts. "
                "unverified_claims are reminders only; unsupported claims were removed."
            ),
        }
        if self.claim_parse_error:
            payload["safety_note"] += " Agent claim JSON was invalid, so no parsed claim may be trusted."
        return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        raise ValueError("empty Local Knowledge Agent response")
    value = text.strip()
    if "```json" in value:
        start = value.find("```json") + 7
        end = value.find("```", start)
        value = value[start:end]
    elif "```" in value:
        start = value.find("```") + 3
        end = value.find("```", start)
        value = value[start:end]
    elif "{" in value and "}" in value:
        value = value[value.find("{"): value.rfind("}") + 1]
    data = json.loads(value.strip())
    if not isinstance(data, dict):
        raise ValueError("Local Knowledge Agent output must be a JSON object")
    return data


def normalize_agent_claims(response: str, result: LocalKnowledgeResult) -> str:
    """Validate Agent claims against URLs returned by the current web search.

    A claim can only remain ``verified`` when the cited URL is one of the provider
    results. Unknown URLs are marked ``unsupported`` and excluded from planner context.
    """
    source_by_url = {source.url: source for source in result.sources}
    claims: List[LocalKnowledgeClaim] = []
    try:
        payload = _extract_json(response)
        raw_claims = payload.get("claims") or []
        if not isinstance(raw_claims, list):
            raise ValueError("claims must be a JSON array")

        for raw in raw_claims:
            if not isinstance(raw, dict):
                continue
            attraction = str(raw.get("attraction") or "").strip()
            claim = str(raw.get("claim") or "").strip()
            if not claim:
                continue

            claim_type = str(raw.get("claim_type") or "other").strip()
            if claim_type not in _ALLOWED_CLAIM_TYPES:
                claim_type = "other"

            status = str(raw.get("verification_status") or "unverified").strip().lower()
            if status not in _ALLOWED_VERIFICATION_STATUS:
                status = "unverified"

            source_url = str(raw.get("source_url") or "").strip() or None
            matched_source = source_by_url.get(source_url or "")
            if status == "verified" and matched_source is None:
                status = "unsupported"
            elif status == "unverified":
                # Unverified statements are never allowed to masquerade behind an arbitrary URL.
                if source_url and matched_source is None:
                    source_url = None

            claims.append(LocalKnowledgeClaim(
                attraction=attraction,
                claim_type=claim_type,
                claim=claim,
                verification_status=status,
                source_url=source_url,
                source_title=matched_source.title if matched_source else None,
            ))

        result.claims = claims
        result.claim_parse_error = None
    except (ValueError, json.JSONDecodeError) as exc:
        result.claims = []
        result.claim_parse_error = str(exc)

    return result.as_planner_context()


def build_local_knowledge_query(
    *,
    city: str,
    attraction_context: str,
    start_date: str,
    end_date: str,
) -> str:
    compact_context = " ".join((attraction_context or "").split())[:1800]
    return (
        f"{city} 旅行景点 官方网站 官方公告 开放时间 预约 闭馆日 门票 入场规则 "
        f"临时关闭 {start_date} {end_date}。候选景点信息：{compact_context}"
    )


def search_local_knowledge(
    *,
    city: str,
    attraction_context: str,
    start_date: str,
    end_date: str,
    api_key: Optional[str] = None,
    max_results: Optional[int] = None,
    opener: Optional[Callable[..., Any]] = None,
) -> LocalKnowledgeResult:
    settings = get_settings()
    effective_key = api_key if api_key is not None else settings.tavily_api_key
    limit = max_results or settings.local_knowledge_max_results
    query = build_local_knowledge_query(
        city=city,
        attraction_context=attraction_context,
        start_date=start_date,
        end_date=end_date,
    )

    if not effective_key:
        return LocalKnowledgeResult(
            query=query,
            degraded=True,
            error="TAVILY_API_KEY not configured",
        )

    payload = {
        "query": query,
        "search_depth": "basic",
        "topic": "general",
        "country": "china",
        "max_results": limit,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    request = Request(
        TAVILY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    open_request = opener or urlopen

    try:
        with open_request(request, timeout=settings.local_knowledge_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return LocalKnowledgeResult(query=query, degraded=True, error=f"HTTP {exc.code}")
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return LocalKnowledgeResult(query=query, degraded=True, error=str(exc))

    sources: List[LocalKnowledgeSource] = []
    for item in body.get("results") or []:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not url or not content:
            continue
        raw_score = item.get("score")
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
        sources.append(LocalKnowledgeSource(title=title, url=url, content=content, score=score))

    return LocalKnowledgeResult(query=query, sources=sources)
