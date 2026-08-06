from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # GCP
    gcp_project_id: str
    gcp_region: str = "us-central1"

    # Database
    database_url: str
    cloud_sql_connection_name: str = ""

    # Gemini (Google Agent Platform / Vertex AI unified SDK)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768

    # Investigation budget defaults
    max_iterations: int = 10
    max_tool_calls: int = 20
    max_tokens: int = 50_000
    confidence_threshold: float = 0.90

    # App
    environment: str = "development"
    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
