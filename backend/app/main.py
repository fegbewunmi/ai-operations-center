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
    # Startup: inject PostgresSaver into the graph
    # Deferred import to avoid importing langgraph-checkpoint-postgres at module level
    # when running tests with MemorySaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.graph import graph as graph_module

    async with await AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        await checkpointer.setup()  # creates langgraph checkpoint tables if not present
        graph_module.investigation_graph = graph_module.build_graph(checkpointer=checkpointer)
        yield
    # Shutdown: nothing to clean up (checkpointer context manager handles connection pool)


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
