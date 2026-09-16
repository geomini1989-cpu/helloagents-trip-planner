"""Official/local attraction knowledge retrieval.

This service deliberately uses a data source that is independent from AMap. AMap is
excellent for POI/location/route data, while operational travel facts such as opening
hours, reservation rules, closure notices and admission policies are better treated as
web/official-source knowledge.

Tavily is the default provider. When it is not configured, callers receive an explicit
degraded result instead of fabricated operating information.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import get_settings


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


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
class LocalKnowledgeResult:
    query: str
    sources: List[LocalKnowledgeSource] = field(default_factory=list)
    provider: str = "tavily"
    degraded: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "provider": self.provider,
            "degraded": self.degraded,
            "error": self.error,
            "sources": [source.to_dict() for source in self.sources],
        }

    def as_agent_context(self) -> str:
        if self.degraded:
            return (
                "官方/本地知识检索当前不可用。不要猜测开放时间、预约、闭馆日、门票或临时公告。"
                f"原因：{self.error or 'unknown'}"
            )
        if not self.sources:
            return "没有检索到足够可靠的官方/本地知识来源；不要自行补造规则。"
        lines = ["以下是 Web Search 检索到的候选来源。优先采用官方/政府/场馆来源，并保留 URL："]
        for index, source in enumerate(self.sources, start=1):
            lines.append(
                f"[{index}] {source.title}\nURL: {source.url}\n摘要: {source.content[:900]}"
            )
        return "\n\n".join(lines)


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
