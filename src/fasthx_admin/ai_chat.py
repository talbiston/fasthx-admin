"""
AI Chat framework for fasthx-admin.

Provides a pluggable AI chat widget with:
- Provider abstraction (ships with OpenAI-compatible)
- Decorator-based tool registry
- Settings stored in DB
- Chat endpoints as a FastAPI router
"""

from __future__ import annotations

import inspect as python_inspect
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, get_type_hints

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import Column, Integer, String, Text, inspect
from sqlalchemy.orm import Session

from .database import Base, get_db, get_engine

logger = logging.getLogger("fasthx_admin.ai_chat")

_PACKAGE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Provider Abstraction
# ---------------------------------------------------------------------------


class AIProvider(ABC):
    """Base class for AI providers."""

    name: str = "base"

    @abstractmethod
    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None, **kwargs
    ) -> dict:
        """Send messages to the AI and return the response.

        Returns dict with keys: response (str), tool_calls (list | None)
        """
        ...

    @abstractmethod
    def get_config_fields(self) -> list[dict]:
        """Return provider-specific settings fields for the settings UI."""
        ...


class OpenAICompatibleProvider(AIProvider):
    """Works with OpenAI, vLLM, Ollama, LiteLLM, etc."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str = "https://api.openai.com",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = 60.0,
        ssl_verify: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.ssl_verify = ssl_verify

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None, **kwargs
    ) -> dict:
        try:
            import httpx
        except ImportError:
            raise RuntimeError(
                "httpx is required for AI chat. Install with: pip install fasthx-admin[ai]"
            )

        url = f"{self.base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=self.timeout, verify=self.ssl_verify) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        tool_calls = message.get("tool_calls")

        return {
            "response": message.get("content") or "",
            "tool_calls": tool_calls,
        }

    def get_config_fields(self) -> list[dict]:
        return [
            {"key": "base_url", "label": "API Base URL", "type": "text", "default": "https://api.openai.com"},
            {"key": "api_key", "label": "API Key", "type": "password", "default": ""},
            {"key": "model", "label": "Model", "type": "text", "default": "gpt-4o-mini"},
            {"key": "temperature", "label": "Temperature", "type": "number", "default": "0.7", "step": "0.1", "min": "0", "max": "2"},
            {"key": "max_tokens", "label": "Max Tokens", "type": "number", "default": "2048"},
            {"key": "timeout", "label": "Timeout (seconds)", "type": "number", "default": "60"},
            {"key": "ssl_verify", "label": "Verify SSL", "type": "checkbox", "default": "true"},
        ]


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------


class ToolDef:
    """Metadata for a registered tool."""

    def __init__(self, func: Callable, name: str, description: str, parameters: dict):
        self.func = func
        self.name = name
        self.description = description
        self.parameters = parameters


class ToolRegistry:
    """Decorator-based tool registration."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def tool(self, name: str | None = None, description: str | None = None):
        """Decorator to register a function as an AI tool."""

        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            tool_desc = description or (func.__doc__ or "").strip() or tool_name
            params = self._extract_parameters(func)
            self._tools[tool_name] = ToolDef(func, tool_name, tool_desc, params)
            return func

        return decorator

    def _extract_parameters(self, func: Callable) -> dict:
        """Extract parameter schema from type hints."""
        sig = python_inspect.signature(func)
        hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
        properties = {}
        required = []

        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
        }

        for param_name, param in sig.parameters.items():
            if param_name == "db":
                continue
            hint = hints.get(param_name)
            if hint is None:
                continue
            json_type = type_map.get(hint, "string")
            properties[param_name] = {"type": json_type, "description": param_name}
            if param.default is python_inspect.Parameter.empty:
                required.append(param_name)

        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def get_openai_tools(self, enabled_tools: set[str] | None = None) -> list[dict]:
        """Return tools in OpenAI function-calling format."""
        tools = []
        for tool_def in self._tools.values():
            if enabled_tools is not None and tool_def.name not in enabled_tools:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": tool_def.name,
                    "description": tool_def.description,
                    "parameters": tool_def.parameters,
                },
            })
        return tools

    async def execute(self, name: str, arguments: dict, db: Session | None = None) -> str:
        """Execute a registered tool by name."""
        tool_def = self._tools.get(name)
        if not tool_def:
            return f"Error: Unknown tool '{name}'"
        try:
            sig = python_inspect.signature(tool_def.func)
            kwargs = dict(arguments)
            if "db" in sig.parameters and db is not None:
                kwargs["db"] = db
            if python_inspect.iscoroutinefunction(tool_def.func):
                result = await tool_def.func(**kwargs)
            else:
                result = tool_def.func(**kwargs)
            return str(result)
        except Exception as e:
            logger.exception("Tool execution failed: %s", name)
            return f"Error executing tool '{name}': {e}"

    def list_tools(self) -> list[dict]:
        """List all registered tools with metadata."""
        return [
            {"name": t.name, "description": t.description}
            for t in self._tools.values()
        ]


# Module-level singleton
tool_registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Chat Handler
# ---------------------------------------------------------------------------


class AIChatHandler:
    """Manages chat sessions, history, and AI calls."""

    def __init__(self, provider: AIProvider, registry: ToolRegistry):
        self.provider = provider
        self.registry = registry

    async def chat(
        self,
        message: str,
        history: list[dict],
        system_prompt: str,
        enabled_tools: set[str] | None = None,
        db: Session | None = None,
    ) -> dict:
        """Process a chat message and return the response."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        tools = self.registry.get_openai_tools(enabled_tools)
        result = await self.provider.chat(messages, tools=tools or None)

        tool_calls_made = []

        # Handle tool calls
        if result.get("tool_calls"):
            # Add assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": result.get("response") or None,
                "tool_calls": result["tool_calls"],
            })

            for tc in result["tool_calls"]:
                func = tc["function"]
                try:
                    args = json.loads(func["arguments"]) if isinstance(func["arguments"], str) else func["arguments"]
                except json.JSONDecodeError:
                    args = {}

                tool_result = await self.registry.execute(func["name"], args, db=db)
                tool_calls_made.append({
                    "name": func["name"],
                    "arguments": args,
                    "result": tool_result,
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

            # Get final response after tool calls
            final = await self.provider.chat(messages)
            result["response"] = final["response"]

        return {
            "response": result["response"],
            "tool_calls": tool_calls_made,
        }


# ---------------------------------------------------------------------------
# Settings Model
# ---------------------------------------------------------------------------


class AIChatSettings(Base):
    __tablename__ = "fasthx_admin_ai_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(255), unique=True, nullable=False)
    value = Column(Text)


def ensure_ai_tables():
    """Create the AI settings table if it doesn't exist."""
    engine = get_engine()
    AIChatSettings.__table__.create(bind=engine, checkfirst=True)


def _get_settings(db: Session) -> dict[str, str]:
    """Load all AI settings from DB as a dict."""
    rows = db.query(AIChatSettings).all()
    return {row.key: row.value for row in rows}


def _save_settings(db: Session, settings: dict[str, str]):
    """Save settings dict to DB (upsert)."""
    for key, value in settings.items():
        row = db.query(AIChatSettings).filter(AIChatSettings.key == key).first()
        if row:
            row.value = value
        else:
            db.add(AIChatSettings(key=key, value=value))
    db.commit()


# ---------------------------------------------------------------------------
# In-memory session store (fallback when no session middleware)
# ---------------------------------------------------------------------------

_chat_sessions: dict[str, list[dict]] = {}
_MAX_HISTORY = 50


def _get_session_id(request: Request) -> str:
    """Get or create a chat session ID from cookies."""
    return request.cookies.get("fasthx_chat_sid", "")


def _get_history(session_id: str) -> list[dict]:
    if not session_id:
        return []
    history = _chat_sessions.get(session_id, [])
    return history[-_MAX_HISTORY:]


def _save_history(session_id: str, history: list[dict]):
    _chat_sessions[session_id] = history[-_MAX_HISTORY:]


# ---------------------------------------------------------------------------
# Settings cache
# ---------------------------------------------------------------------------

_settings_cache: dict[str, str] = {}
_settings_cache_time: float = 0
_CACHE_TTL = 30  # seconds


def _get_cached_settings(db: Session) -> dict[str, str]:
    global _settings_cache, _settings_cache_time
    now = time.time()
    if now - _settings_cache_time > _CACHE_TTL:
        _settings_cache = _get_settings(db)
        _settings_cache_time = now
    return _settings_cache


def _invalidate_settings_cache():
    global _settings_cache_time
    _settings_cache_time = 0


def is_chat_widget_enabled() -> bool:
    """Check if the AI chat widget is enabled in DB settings (uses cache)."""
    global _settings_cache, _settings_cache_time
    now = time.time()
    if now - _settings_cache_time > _CACHE_TTL or not _settings_cache:
        try:
            db = next(get_db())
            try:
                _settings_cache = _get_settings(db)
                _settings_cache_time = time.time()
            finally:
                db.close()
        except Exception:
            return False
    return _settings_cache.get("enabled") == "true"


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------


def _build_system_prompt(settings: dict[str, str]) -> str:
    """Build the full system prompt from base + context items."""
    base = settings.get("system_prompt", "You are a helpful admin assistant.")
    context_json = settings.get("context_items", "[]")
    try:
        context_items = json.loads(context_json)
    except (json.JSONDecodeError, TypeError):
        context_items = []

    parts = [base]
    for item in context_items:
        if item.get("enabled", True):
            parts.append(f"\n\n## {item['name']}\n{item['content']}")
    return "\n".join(parts)


def _build_provider(settings: dict[str, str]) -> AIProvider:
    """Build an AI provider from settings."""
    return OpenAICompatibleProvider(
        base_url=settings.get("base_url", "https://api.openai.com"),
        api_key=settings.get("api_key", ""),
        model=settings.get("model", "gpt-4o-mini"),
        temperature=float(settings.get("temperature", "0.7")),
        max_tokens=int(settings.get("max_tokens", "2048")),
        timeout=float(settings.get("timeout", "60")),
        ssl_verify=settings.get("ssl_verify", "true") == "true",
    )


def create_ai_chat_router(admin) -> APIRouter:
    """Create FastAPI router with AI chat endpoints."""
    router = APIRouter(prefix="/ai", tags=["AI Chat"])
    templates = admin.templates

    @router.post("/chat")
    async def chat_endpoint(request: Request, db: Session = Depends(get_db)):
        settings = _get_cached_settings(db)
        if settings.get("enabled") != "true":
            return JSONResponse({"error": "AI chat is not enabled"}, status_code=400)

        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            return JSONResponse({"error": "Empty message"}, status_code=400)

        session_id = _get_session_id(request)
        if not session_id:
            session_id = str(uuid.uuid4())
        history = _get_history(session_id)

        provider = _build_provider(settings)
        system_prompt = _build_system_prompt(settings)

        # Determine enabled tools
        enabled_tools_json = settings.get("enabled_tools", "[]")
        try:
            enabled_tools = set(json.loads(enabled_tools_json))
        except (json.JSONDecodeError, TypeError):
            enabled_tools = set()

        handler = AIChatHandler(provider, tool_registry)
        try:
            result = await handler.chat(
                message, history, system_prompt,
                enabled_tools=enabled_tools or None,
                db=db,
            )
        except Exception as e:
            logger.exception("AI chat error")
            return JSONResponse({"error": str(e)}, status_code=500)

        # Update history
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": result["response"]})
        _save_history(session_id, history)

        response = JSONResponse({
            "response": result["response"],
            "tool_calls": result["tool_calls"],
        })
        if not request.cookies.get("fasthx_chat_sid"):
            response.set_cookie("fasthx_chat_sid", session_id, httponly=True, samesite="lax")
        return response

    @router.post("/clear")
    async def clear_chat(request: Request):
        session_id = _get_session_id(request)
        if session_id:
            _chat_sessions.pop(session_id, None)
        return JSONResponse({"status": "ok"})

    @router.get("/history")
    async def get_history(request: Request):
        session_id = _get_session_id(request)
        history = _get_history(session_id)
        return JSONResponse({"messages": history})

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, db: Session = Depends(get_db)):
        settings = _get_settings(db)
        provider = OpenAICompatibleProvider()
        return templates.TemplateResponse("ai_settings.html", {
            "request": request,
            "settings": settings,
            "config_fields": provider.get_config_fields(),
            "active_page": "ai_settings",
        })

    @router.post("/settings", response_class=HTMLResponse)
    async def save_settings(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        settings_to_save = {}
        checkbox_keys = {"enabled", "ssl_verify"}
        for key in ["enabled", "base_url", "api_key", "model", "temperature",
                     "max_tokens", "timeout", "ssl_verify", "system_prompt"]:
            value = form.get(key, "")
            if key in checkbox_keys:
                value = "true" if value else "false"
            settings_to_save[key] = str(value)

        # Don't overwrite api_key if masked placeholder sent
        if settings_to_save.get("api_key") == "********":
            existing = _get_settings(db)
            settings_to_save["api_key"] = existing.get("api_key", "")

        _save_settings(db, settings_to_save)
        _invalidate_settings_cache()

        settings = _get_settings(db)
        provider = OpenAICompatibleProvider()
        return templates.TemplateResponse("ai_settings.html", {
            "request": request,
            "settings": settings,
            "config_fields": provider.get_config_fields(),
            "active_page": "ai_settings",
            "save_success": True,
        })

    @router.get("/settings/context", response_class=HTMLResponse)
    async def context_settings_page(request: Request, db: Session = Depends(get_db)):
        settings = _get_settings(db)
        context_items = []
        try:
            context_items = json.loads(settings.get("context_items", "[]"))
        except (json.JSONDecodeError, TypeError):
            pass

        enabled_tools: list[str] = []
        try:
            enabled_tools = json.loads(settings.get("enabled_tools", "[]"))
        except (json.JSONDecodeError, TypeError):
            pass

        return templates.TemplateResponse("ai_context_settings.html", {
            "request": request,
            "context_items": context_items,
            "tools": tool_registry.list_tools(),
            "enabled_tools": enabled_tools,
            "active_page": "ai_context_settings",
        })

    @router.post("/settings/context", response_class=HTMLResponse)
    async def save_context_settings(request: Request, db: Session = Depends(get_db)):
        form = await request.form()

        # Parse context items from form
        context_items = []
        idx = 0
        while True:
            name = form.get(f"context_name_{idx}")
            if name is None:
                break
            content = form.get(f"context_content_{idx}", "")
            enabled = form.get(f"context_enabled_{idx}") == "on"
            if name.strip():
                context_items.append({
                    "name": name.strip(),
                    "content": content,
                    "enabled": enabled,
                })
            idx += 1

        # Parse enabled tools
        enabled_tools = []
        for tool_info in tool_registry.list_tools():
            if form.get(f"tool_{tool_info['name']}") == "on":
                enabled_tools.append(tool_info["name"])

        _save_settings(db, {
            "context_items": json.dumps(context_items),
            "enabled_tools": json.dumps(enabled_tools),
        })
        _invalidate_settings_cache()

        settings = _get_settings(db)
        return templates.TemplateResponse("ai_context_settings.html", {
            "request": request,
            "context_items": context_items,
            "tools": tool_registry.list_tools(),
            "enabled_tools": enabled_tools,
            "active_page": "ai_context_settings",
            "save_success": True,
        })

    return router
