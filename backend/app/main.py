from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.api.v1.investigations import router as investigations_router
from app.config import settings
from app.db.session import engine
from app.graph.graph import build_graph


def _setup_telemetry() -> None:
    """Configure OpenTelemetry to export traces to Cloud Trace."""
    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(CloudTraceSpanExporter(project_id=settings.gcp_project_id))
    )
    trace.set_tracer_provider(provider)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    # AsyncPostgresSaver uses psycopg directly - strip the SQLAlchemy driver prefix.
    psycopg_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(psycopg_url) as checkpointer:
        await checkpointer.setup()
        # Store on app.state so every request handler can access the live graph.
        app.state.investigation_graph = build_graph(checkpointer=checkpointer)
        yield


def create_app() -> FastAPI:
    _setup_telemetry()

    app = FastAPI(
        title="AI Operations Center",
        version="0.1.0",
        description="Multi-agent incident investigation system for Orion Commerce",
        lifespan=lifespan,
    )

    app.include_router(investigations_router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "environment": settings.environment}

    FastAPIInstrumentor.instrument_app(app)
    return app


app = create_app()
