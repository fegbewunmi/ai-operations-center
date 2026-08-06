import logging
from datetime import datetime, timezone
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.graph.state import InvestigationState
from app.shared.schemas.core import AgentError, TimelineEvent
from app.shared.schemas.knowledge import KnowledgeContext, KnowledgeResult, ServiceOwnership
from app.shared.schemas.planner import KnowledgeQuery

logger = logging.getLogger(__name__)


async def _embed_query(query_text: str) -> list[float] | None:
    """Generate an embedding for the query via the Gemini REST API."""
    try:
        import httpx
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.embedding_model}:embedContent"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                params={"key": settings.gemini_api_key},
                json={
                    "model": f"models/{settings.embedding_model}",
                    "content": {"parts": [{"text": query_text}]},
                    "outputDimensionality": settings.embedding_dimensions,
                },
            )
            resp.raise_for_status()
            return resp.json()["embedding"]["values"]
    except Exception:
        return None


async def _search_documents(
    query_text: str,
    service_name: str | None,
    doc_types: list[str] | None,
    limit: int = 5,
) -> list[KnowledgeResult]:
    """
    Semantic search against the documents table using pgvector.
    Falls back to keyword search if embedding fails.
    """
    embedding = await _embed_query(query_text)

    async with AsyncSessionLocal() as db:
        if embedding is not None:
            # Format embedding as a PostgreSQL vector literal and inline it directly
            # to avoid asyncpg type-binding issues with the ::vector cast.
            embedding_literal = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"

            type_filter = ""
            svc_filter = ""
            params: dict = {"limit": limit}

            if doc_types:
                type_filter = "AND document_type = ANY(:doc_types)"
                params["doc_types"] = doc_types
            if service_name:
                svc_filter = "AND (service_name = :service_name OR service_name IS NULL)"
                params["service_name"] = service_name

            rows = await db.execute(
                text(f"""
                    SELECT document_id::text, document_type, title,
                           content,
                           embedding <=> '{embedding_literal}'::vector AS distance
                    FROM documents
                    WHERE 1=1 {type_filter} {svc_filter}
                    ORDER BY distance ASC
                    LIMIT :limit
                """),
                params,
            )
        else:
            # Keyword fallback: match any word from the query
            words = [w for w in query_text.split() if len(w) > 3][:5]
            if not words:
                words = [query_text[:20]]
            conditions = " OR ".join(
                f"(title ILIKE :w{i} OR content ILIKE :w{i})"
                for i in range(len(words))
            )
            params_kw: dict = {f"w{i}": f"%{w}%" for i, w in enumerate(words)}
            params_kw["limit"] = limit
            rows = await db.execute(
                text(f"""
                    SELECT document_id::text, document_type, title,
                           content, 0.5 AS distance
                    FROM documents
                    WHERE {conditions}
                    LIMIT :limit
                """),
                params_kw,
            )

        raw = rows.mappings().all()

    results = []
    for row in raw:
        excerpt = row["content"][:400] + "..." if len(row["content"]) > 400 else row["content"]
        relevance = max(0.0, 1.0 - float(row["distance"]))
        results.append(KnowledgeResult(
            document_id=row["document_id"],
            document_type=row["document_type"],
            title=row["title"],
            excerpt=excerpt,
            relevance_score=round(relevance, 3),
        ))

    return results


async def _fetch_service_ownership(service_name: str) -> ServiceOwnership | None:
    """Look up service ownership by joining services and service_ownership tables."""
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("""
                SELECT s.name, o.team_name, o.slack_channel,
                       o.pagerduty_rotation, o.runbook_url, o.oncall_contact
                FROM services s
                JOIN service_ownership o ON o.service_id = s.service_id
                WHERE s.name = :name
            """),
            {"name": service_name},
        )
        svc = row.mappings().fetchone()

    if svc is None:
        return None

    return ServiceOwnership(
        service=svc["name"],
        team=svc.get("team_name") or "unknown",
        slack_channel=svc.get("slack_channel") or "#platform-alerts",
        pagerduty_rotation=svc.get("pagerduty_rotation"),
        runbook_url=svc.get("runbook_url"),
        oncall_contact=svc.get("oncall_contact"),
    )


async def _generate_knowledge_summary(
    results: list[KnowledgeResult],
    query: str,
    service_name: str | None,
    ownership: ServiceOwnership | None,
    llm: Any = None,
) -> str:
    if llm is None:
        llm = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
            temperature=0.1,
        )

    if not results:
        doc_context = "No relevant documents found in the knowledge base."
    else:
        doc_lines = [
            f"  [{r.document_type}] {r.title} (relevance: {r.relevance_score:.2f})\n"
            f"    {r.excerpt[:200]}"
            for r in results
        ]
        doc_context = "Relevant documents:\n" + "\n".join(doc_lines)

    ownership_context = ""
    if ownership:
        ownership_context = (
            f"\nService ownership: team={ownership.team}, "
            f"slack={ownership.slack_channel}"
            + (f", runbook={ownership.runbook_url}" if ownership.runbook_url else "")
        )

    prompt = (
        f"Investigation question: {query}\n"
        f"Service: {service_name or 'unknown'}\n\n"
        f"{doc_context}{ownership_context}\n\n"
        "In 2-3 sentences: summarise what the knowledge base reveals about this incident. "
        "If relevant runbooks or postmortems were found, note the key remediation steps. "
        "If nothing was found, state that and suggest what to investigate next."
    )

    response = await llm.ainvoke([
        SystemMessage(content="You are an SRE reviewing runbooks and postmortems during an incident."),
        HumanMessage(content=prompt),
    ])
    return response.content.strip()


async def knowledge_node(state: InvestigationState) -> dict:
    """
    Retrieves relevant runbooks, postmortems, and architecture docs via pgvector.
    Appends KnowledgeContext and a TimelineEvent to state.
    """
    decision = state["planner_decision"]
    query: KnowledgeQuery = decision.query  # type: ignore[assignment]

    try:
        results = await _search_documents(
            query_text=query.query,
            service_name=query.service_name,
            doc_types=query.document_types,
        )
    except Exception as exc:
        logger.error("knowledge_node _search_documents failed: %s", exc, exc_info=True)
        return {
            "error_log": [AgentError(
                agent="knowledge",
                query_summary=query.query,
                error_type="tool_failure",
                message=str(exc),
                timestamp=datetime.now(timezone.utc),
                retries_attempted=0,
            )],
            "phase": "planning",
        }

    ownership: ServiceOwnership | None = None
    if query.service_name:
        try:
            ownership = await _fetch_service_ownership(query.service_name)
        except Exception:
            pass

    try:
        summary = await _generate_knowledge_summary(
            results=results,
            query=query.query,
            service_name=query.service_name,
            ownership=ownership,
        )
    except Exception as exc:
        summary = f"Summary generation failed: {exc}"

    context = KnowledgeContext(
        query=query.query,
        results=results,
        ownership=ownership,
        summary=summary,
    )

    event = TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="investigation_finding",
        service=query.service_name or "unknown",
        description=(
            f"Knowledge search: {len(results)} document(s) found for '{query.query[:60]}'."
        ),
        source="knowledge_agent",
    )

    return {
        "knowledge_context": [context],
        "timeline": [event],
        "phase": "planning",
    }
