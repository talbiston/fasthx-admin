"""
fasthx-admin — FastAPI + HTMX + Jinja2 admin interface framework.

A modern replacement for Flask-Admin with full control over rendering,
HTMX interactions, dark/light theming, and OIDC authentication.
"""

from .crud import Admin, CRUDView, COLUMN_TYPE_MAP, toast_response, DEFAULT_TOAST_DELAYS, refresh_list_response, modal_response, console_response, console_sse_message, ansi_to_html, ValidationError, celery_send_task
from .wizard import WizardView
from .database import Base, init_db, get_db, get_engine, db_session, get_pool_status
from .auth import get_current_user, OidcAuthenticator, AuthError, AUTH_DISABLED
from .ai_chat import ToolRegistry, tool_registry, AIProvider, OpenAICompatibleProvider, ai_complete

# Derived from installed dist metadata so it cannot drift from pyproject.toml.
# The literal is only a fallback for an uninstalled source checkout.
from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("fasthx-admin")
except PackageNotFoundError:
    __version__ = "0.6.4"

__all__ = [
    "Admin",
    "CRUDView",
    "WizardView",
    "COLUMN_TYPE_MAP",
    "toast_response",
    "DEFAULT_TOAST_DELAYS",
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
    "db_session",
    "get_pool_status",
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
