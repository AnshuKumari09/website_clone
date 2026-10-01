from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    groq_api_key: str
    code_model: str = "openai/gpt-oss-20b"
    vision_model: str = "qwen/qwen3.8-27b"
    generation_model: str = "claude-sonnet-5"
    # backend/app/config.py -> parents[2] is the project root
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()