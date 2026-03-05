"""
fasthx-admin — FastAPI + HTMX + Jinja2 admin interface framework.

A modern replacement for Flask-Admin with full control over rendering,
HTMX interactions, dark/light theming, and OIDC authentication.
"""

from .crud import Admin, CRUDView, COLUMN_TYPE_MAP
from .database import Base, init_db, get_db, get_engine
from .auth import get_current_user, oidc_login, AuthError, AUTH_DISABLED
from .ai_chat import ToolRegistry, tool_registry, AIProvider, OpenAICompatibleProvider

__version__ = "0.2.1"

__all__ = [
    "Admin",
    "CRUDView",
    "COLUMN_TYPE_MAP",
    "Base",
    "init_db",
    "get_db",
    "get_engine",
    "get_current_user",
    "oidc_login",
    "AuthError",
    "AUTH_DISABLED",
    "ToolRegistry",
    "tool_registry",
    "AIProvider",
    "OpenAICompatibleProvider",
]
