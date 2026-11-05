"""Configuration re-export for backwards compatibility. Implementation moved to app.core.config."""
from app.core.config import Settings, settings

__all__ = ["Settings", "settings"]
