import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Scalpyn API"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://scalpyn:scalpyn@localhost:5432/scalpyn")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "supersecret")
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef")

settings = Settings()
