"""Dashboard re-export for backwards compatibility. Implementation moved to app.dashboard.router."""
from app.dashboard.router import router, is_authenticated, require_auth

__all__ = ["router", "is_authenticated", "require_auth"]
