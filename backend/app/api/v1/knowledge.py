"""
Knowledge base search endpoint.

Thin wrapper around app.graph.nodes.knowledge._search_documents so callers
outside the investigation graph (e.g. the MCP server in app/mcp/) can query
runbooks, postmortems, and other documents directly over HTTP instead of only
via a planner-driven KnowledgeQuery inside a running investigation.
"""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.graph.nodes.knowledge import _search_documents
from app.shared.schemas.knowledge import KnowledgeResult

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    service_name: str | None = None
    document_types: list[Literal["runbook", "postmortem", "architecture_doc", "error_pattern"]] | None = None
    limit: int = 5


@router.post("/search", response_model=list[KnowledgeResult])
async def search_knowledge(body: KnowledgeSearchRequest) -> list[KnowledgeResult]:
    """Semantic (pgvector) search over documents, with a keyword fallback if embedding fails."""
    return await _search_documents(
        query_text=body.query,
        service_name=body.service_name,
        doc_types=body.document_types,
        limit=body.limit,
    )
