"""Application configuration and environment settings."""

import os
from enum import Enum
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Base project paths
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class PipelineMode(str, Enum):
    """Pipeline execution rollout modes."""
    DRY_RUN = "DRY_RUN"              # Log decisions only; do not send emails
    REDIRECTED = "REDIRECTED"        # Dispatches emails redirected to test address
    COHORT = "COHORT"                # Live send limited to a small whitelist/cohort
    PROD = "PROD"                    # Full production dispatch to real fellows


class Settings:
    """Strongly typed application configuration."""
    
    # Storage settings
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'pipeline.db'}")
    
    # Execution & rollout settings
    PIPELINE_MODE: PipelineMode = PipelineMode(os.getenv("PIPELINE_MODE", "DRY_RUN").upper())
    REDIRECT_TEST_EMAIL: str = os.getenv("REDIRECT_TEST_EMAIL", "test@example.com")
    COHORT_FELLOW_IDS: list[int] = [
        int(x.strip()) for x in os.getenv("COHORT_FELLOW_IDS", "1").split(",") if x.strip()
    ]
    
    # API simulation settings
    USE_MOCK_APIS: bool = os.getenv("USE_MOCK_APIS", "true").lower() in ("true", "1", "yes")
    
    # Outbox directory for mock/redirected email previews
    OUTBOX_DIR: Path = BASE_DIR / "outbox"
    
    # Reference tracker spreadsheet
    TRACKER_XLSX_PATH: Path = (
        BASE_DIR / "data" / "YouTube_Pipeline_Tracker.xlsx"
        if (BASE_DIR / "data" / "YouTube_Pipeline_Tracker.xlsx").exists()
        else BASE_DIR / "references" / "YouTube_Pipeline_Tracker.xlsx"
    )


settings = Settings()
