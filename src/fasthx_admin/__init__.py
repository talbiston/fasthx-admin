"""
fasthx-admin — FastAPI + HTMX + Jinja2 admin interface framework.

A modern replacement for Flask-Admin with full control over rendering,
HTMX interactions, dark/light theming, and OIDC authentication.
"""

from .crud import Admin, CRUDView, COLUMN_TYPE_MAP, toast_response, refresh_list_response, modal_response, console_response, console_sse_message, ansi_to_html, ValidationError, celery_send_task
from .database import Base, init_db, get_db, get_engine
from .auth import get_current_user, OidcAuthenticator, AuthError, AUTH_DISABLED
from .ai_chat import ToolRegistry, tool_registry, AIProvider, OpenAICompatibleProvider, ai_complete

__version__ = "0.5.71"

__all__ = [
    "Admin",
    "CRUDView",
    "COLUMN_TYPE_MAP",
    "toast_response",
    "refresh_list_response",
    "modal_response",
    "console_response",
    "console_sse_message",
    "ansi_to_html",
    "ValidationError",
    "Base",
    "init_db",
    "get_db",
    "get_engine",
    "get_current_user",
    "OidcAuthenticator",
    "AuthError",
    "AUTH_DISABLED",
    "ToolRegistry",
    "tool_registry",
    "AIProvider",
    "OpenAICompatibleProvider",
    "ai_complete",
    "celery_send_task",
]
