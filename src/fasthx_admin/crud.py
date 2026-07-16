"""
Reusable CRUD view generator that introspects SQLAlchemy models
to auto-generate FastAPI routes + Jinja2 templates.

This replaces Flask-Admin's ModelView with full control over rendering.
"""

from __future__ import annotations

import csv
from urllib.parse import quote_plus, urlencode
import functools
import io
import json
import math
import os
import re
from collections import defaultdict
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, or_, String, cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Join
from celery import Celery

from .auth import get_current_user
from .database import get_db

_PACKAGE_DIR = Path(__file__).resolve().parent

# Hard cap on multi_row_select_all_pages selection size. Bulk actions POST
# ids as a single comma-separated `ids` form field, so the ceiling here is
# really about the action handler's memory + DB IN(...) clause cost, not
# multipart field count.
MULTI_ROW_SELECT_LIMIT = 50_000

# Maps SQLAlchemy column types to HTML input types
COLUMN_TYPE_MAP = {
    "Integer": "number",
    "String": "text",
    "VARCHAR": "text",
    "Text": "textarea",
    "Boolean": "checkbox",
    "Float": "number",
    "DateTime": "datetime-local",
    "Date": "date",
    "Enum": "select",
}


def _secure_filename(name: str) -> str:
    """Strip directory components and unsafe characters from an upload name."""
    name = os.path.basename(name or "")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return name or "upload"

# Maps SQLAlchemy column type names to available filter operations.
# Each operation is (key, label) where key is used in the URL.
FILTER_OPS_STRING = [
    ("contains", "contains"),
    ("not_contains", "not contains"),
    ("equals", "equals"),
    ("not_equal", "not equal"),
    ("empty", "empty"),
    ("in_list", "in list"),
]
FILTER_OPS_NUMERIC = [
    ("equals", "equals"),
    ("not_equal", "not equal"),
    ("greater", "greater than"),
    ("smaller", "smaller than"),
    ("empty", "empty"),
]
FILTER_OPS_BOOLEAN = [
    ("equals", "equals"),
    ("not_equal", "not equal"),
]
FILTER_OPS_DATE = [
    ("equals", "equals"),
    ("not_equal", "not equal"),
    ("greater", "greater than"),
    ("smaller", "smaller than"),
    ("empty", "empty"),
]

# Map SQLAlchemy type names to filter operation lists
FILTER_TYPE_OPS = {
    "String": FILTER_OPS_STRING,
    "VARCHAR": FILTER_OPS_STRING,
    "Text": FILTER_OPS_STRING,
    "Integer": FILTER_OPS_NUMERIC,
    "Float": FILTER_OPS_NUMERIC,
    "Boolean": FILTER_OPS_BOOLEAN,
    "DateTime": FILTER_OPS_DATE,
    "Date": FILTER_OPS_DATE,
    "Enum": FILTER_OPS_STRING,
}

# Global registry of model classes by table name, populated during CRUDView init
_model_registry: Dict[str, Any] = {}


def _joined_table_names(query) -> set:
    """Table names already present in *query*'s FROM clause / JOINs.

    Lets a later join site skip a table that an earlier one (the search path,
    a column filter, or a subclass ``_build_query`` override) has already
    joined, avoiding a duplicate ``LEFT OUTER JOIN`` that Postgres rejects with
    ``DuplicateAlias``. Introspects the compiled FROM list, so it is agnostic
    to *how* the join was added.
    """
    names: set = set()

    def _walk(fc):
        if isinstance(fc, Join):  # ORM joins are _ORMJoin, a Join subclass
            _walk(fc.left)
            _walk(fc.right)
        else:
            name = getattr(fc, "name", None)
            if name:
                names.add(name)

    for fc in query.selectable.get_final_froms():
        _walk(fc)
    return names


class ValidationError(Exception):
    """Raised from ``CRUDView.on_model_change`` to abort a create/edit.

    Usage::

        class MyView(CRUDView):
            model = MyModel

            def on_model_change(self, item, form_data, is_new, db, request=None):
                if not item.hostname:
                    raise ValidationError("Hostname is required")
                if not item.hostname.endswith(".local"):
                    raise ValidationError("Hostname must end with .local")
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def toast_response(
    message: str | None = None,
    type: str = "info",
    title: str | None = None,
    redirect: str | None = None,
    status_code: int = 200,
    request=None,
    refresh: bool = False,
) -> HTMLResponse:
    """Return an HTMLResponse that shows a toast and optionally navigates.

    A single helper for the three things a custom endpoint typically wants to do
    after an action. Pick the navigation mode with an argument:

    * **No navigation** (default) — just show the toast::

          return toast_response("Saved!", type="success")

    * **Inline list refresh** (``refresh=True``) — re-render only the current
      list view's ``#table-body`` in place via ``HX-Location``, preserving its
      search ``q``, ``flt*``/``cf_*`` filters, ``sort``/``order`` and ``page``.
      This is what custom ``row_actions`` should use so the active search/filter
      survives. No full-page reload, scroll position preserved. Requires
      ``request`` (its ``Referer`` carries the list state); with no usable
      ``Referer`` it falls back to a plain toast::

          return toast_response("Deployed!", type="success", refresh=True, request=request)

    * **Full-page redirect** (``redirect=...``) — navigate to a URL via
      ``HX-Redirect``. Pass ``request`` too and, when the redirect targets the
      same path as the ``Referer``, the list's query string is preserved::

          return toast_response("Created!", type="success", redirect=f"/{self.name}", request=request)

    Args:
        message: Toast message text. Omit to navigate/refresh with no toast.
        type: One of "success", "danger", "warning", "info".
        title: Optional title (defaults to capitalised type).
        redirect: Optional URL — full-page ``HX-Redirect`` navigation after toast.
        status_code: HTTP status code (default 200).
        request: FastAPI ``Request`` — its ``Referer`` carries list state. Required
            for ``refresh=True``; optional for ``redirect`` (enables state-preserving
            redirect when paths match).
        refresh: When True, refresh the current list view's table body in place
            instead of (or before falling back from) navigating. Takes precedence
            over ``redirect``.
    """
    import urllib.parse
    from urllib.parse import urlparse, parse_qsl, urlencode

    toast_data: Optional[Dict[str, Any]] = None
    if message:
        toast_data = {"message": message, "type": type}
        if title:
            toast_data["title"] = title

    headers: Dict[str, str] = {}

    # Mode 1 — inline refresh of the current list view's table body.
    if refresh:
        referer = request.headers.get("referer") if request else None
        if referer:
            parsed = urlparse(referer)
            ref_path, ref_query = parsed.path, parsed.query
            # Re-use the Referer's query string (search/filter/sort/page) for the
            # path, and pass ``partial=1`` via ``values`` so the list endpoint
            # returns the table_body fragment while the pushed URL stays clean.
            params = [(k, v) for k, v in parse_qsl(ref_query, keep_blank_values=False) if k != "partial"]
            location = {
                "path": ref_path + (("?" + urlencode(params)) if params else ""),
                "target": "#table-body",
                "swap": "innerHTML",
                "values": {"partial": "1"},
            }
            headers["HX-Location"] = json.dumps(location)
            if toast_data:
                headers["HX-Trigger"] = json.dumps({"showToast": toast_data})
            return HTMLResponse("", status_code=status_code, headers=headers)
        # No usable Referer — fall through to a plain toast (nothing is lost).

    # Mode 2 — full-page redirect (optionally preserving list state via Referer).
    if redirect:
        if request:
            referer = request.headers.get("referer")
            if referer:
                ref_parsed = urlparse(referer)
                redirect_parsed = urlparse(redirect)
                # Only use Referer if it matches the same path as the redirect
                if ref_parsed.path.rstrip("/") == redirect_parsed.path.rstrip("/") and ref_parsed.query:
                    redirect = referer if "://" not in redirect else ref_parsed.path + "?" + ref_parsed.query
        # Pass the toast as a cookie so it survives the full page navigation.
        headers["HX-Redirect"] = redirect
        response = HTMLResponse("", status_code=status_code, headers=headers)
        if toast_data:
            response.set_cookie(
                "_toast",
                urllib.parse.quote(json.dumps(toast_data)),
                max_age=10,
                httponly=False,
                samesite="lax",
            )
        return response

    # Mode 3 — plain toast, no navigation.
    if toast_data:
        headers["HX-Trigger"] = json.dumps({"showToast": toast_data})
    return HTMLResponse("", status_code=status_code, headers=headers)


def refresh_list_response(
    request,
    message: str | None = None,
    type: str = "success",
    title: str | None = None,
    full_reload: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    """Deprecated alias for :func:`toast_response` with ``refresh=True``.

    Kept for backwards compatibility (this helper shipped in 0.5.54). New code
    should call ``toast_response`` directly::

        toast_response(message, refresh=True, request=request)        # in-place list refresh
        toast_response(message, redirect=referer, request=request)    # full-page reload

    ``full_reload=True`` maps to a full-page ``HX-Redirect`` back to the list
    (via the ``Referer``); the default maps to the in-place table-body refresh.
    """
    if full_reload:
        referer = request.headers.get("referer") if request else None
        if referer:
            return toast_response(
                message, type=type, title=title,
                redirect=referer, request=request, status_code=status_code,
            )
        # No Referer to redirect back to — just show the toast.
        return toast_response(message, type=type, title=title, status_code=status_code)
    return toast_response(
        message, type=type, title=title,
        refresh=True, request=request, status_code=status_code,
    )


def modal_response(
    title: str,
    body: str,
    actions: Optional[List[Dict[str, Any]]] = None,
    size: Optional[str] = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Return an HTMLResponse that renders content inside the admin modal.

    The response targets ``#admin-modal .modal-content`` via ``HX-Retarget``
    and triggers the ``showModal`` event so the Bootstrap modal opens
    automatically.

    Usage in a custom endpoint::

        @CRUDView.endpoint("/{name}/{item_id}/preview", methods=["GET"])
        async def preview(self, ...):
            return modal_response(
                title="Preview",
                body="<p>Content here</p>",
                actions=[{"label": "Download", "icon": "download",
                          "href": "/download/123", "css_class": "btn btn-primary"}],
                size="modal-lg",
            )

    Args:
        title: Modal header title text (auto-escaped).
        body: HTML string for the modal body (trusted, not escaped).
        actions: Optional list of action button dicts. Each may contain:
            - label (str): Button text.
            - icon (str): Bootstrap icon name (without ``bi-`` prefix).
            - href (str): Link URL (renders as ``<a>``).
            - hx_post (str): HTMX post URL (renders as ``<button>``).
            - hx_get (str): HTMX get URL (renders as ``<button>``).
            - css_class (str): CSS classes (default ``"btn btn-secondary"``).
        size: Optional modal size class (``"modal-lg"``, ``"modal-xl"``,
            ``"modal-sm"``).
        status_code: HTTP status code (default 200).
    """
    import html as html_mod

    safe_title = html_mod.escape(title)

    actions_html = ""
    if actions:
        buttons = []
        for action in actions:
            css_class = action.get("css_class", "btn btn-secondary")
            icon_html = (
                f'<i class="bi bi-{html_mod.escape(action["icon"])}"></i> '
                if action.get("icon")
                else ""
            )
            label = html_mod.escape(action.get("label", ""))
            if action.get("href"):
                buttons.append(
                    f'<a href="{action["href"]}" class="{css_class}">'
                    f"{icon_html}{label}</a>"
                )
            else:
                attrs = []
                if action.get("hx_post"):
                    attrs.append(f'hx-post="{action["hx_post"]}"')
                if action.get("hx_get"):
                    attrs.append(f'hx-get="{action["hx_get"]}"')
                attr_str = " ".join(attrs)
                buttons.append(
                    f'<button type="button" class="{css_class}" {attr_str}>'
                    f"{icon_html}{label}</button>"
                )
        actions_html = "\n        ".join(buttons)

    content = f"""<div class="modal-header">
        <h5 class="modal-title" id="admin-modal-title">{safe_title}</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
    </div>
    <div class="modal-body">
        {body}
    </div>
    <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        {actions_html}
    </div>"""

    trigger_data: Dict[str, Any] = {}
    if size:
        trigger_data["size"] = size

    headers: Dict[str, str] = {
        "HX-Trigger": json.dumps({"showModal": trigger_data}),
        "HX-Retarget": "#admin-modal .modal-content",
        "HX-Reswap": "innerHTML",
    }

    return HTMLResponse(content, status_code=status_code, headers=headers)


# ---------------------------------------------------------------------------
# ANSI → HTML conversion
# ---------------------------------------------------------------------------

_ANSI_SGR_RE = None  # lazy-compiled regex

_ANSI_FG_MAP = {
    "30": "ansi-black",   "31": "ansi-red",      "32": "ansi-green",
    "33": "ansi-yellow",  "34": "ansi-blue",      "35": "ansi-magenta",
    "36": "ansi-cyan",    "37": "ansi-white",
    "90": "ansi-bright-black",  "91": "ansi-bright-red",
    "92": "ansi-bright-green",  "93": "ansi-bright-yellow",
    "94": "ansi-bright-blue",   "95": "ansi-bright-magenta",
    "96": "ansi-bright-cyan",   "97": "ansi-bright-white",
}

_ANSI_STYLE_MAP = {
    "1": "ansi-bold",
    "3": "ansi-italic",
    "4": "ansi-underline",
}


def ansi_to_html(text: str) -> str:
    """Convert ANSI SGR escape sequences in *text* to ``<span>`` elements.

    Supports foreground colours (30-37, 90-97), bold (1), italic (3),
    underline (4), and reset (0).  The text content is HTML-escaped.
    """
    import html as html_mod
    import re

    global _ANSI_SGR_RE
    if _ANSI_SGR_RE is None:
        _ANSI_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

    parts: List[str] = []
    open_spans = 0
    last = 0

    for match in _ANSI_SGR_RE.finditer(text):
        # Append escaped text before this escape sequence
        parts.append(html_mod.escape(text[last:match.start()]))
        last = match.end()

        codes = match.group(1).split(";") if match.group(1) else ["0"]
        for code in codes:
            if code == "0" or code == "":
                # Reset — close all open spans
                parts.append("</span>" * open_spans)
                open_spans = 0
            elif code in _ANSI_FG_MAP:
                parts.append(f'<span class="{_ANSI_FG_MAP[code]}">')
                open_spans += 1
            elif code in _ANSI_STYLE_MAP:
                parts.append(f'<span class="{_ANSI_STYLE_MAP[code]}">')
                open_spans += 1

    # Remaining text
    parts.append(html_mod.escape(text[last:]))
    # Close any unclosed spans
    parts.append("</span>" * open_spans)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Terminal console response
# ---------------------------------------------------------------------------

def console_response(
    title: str,
    output: str,
    *,
    input_enabled: bool = False,
    input_action: Optional[str] = None,
    input_placeholder: str = "$ ",
    stream_url: Optional[str] = None,
    stream_event: str = "output",
    ansi: bool = True,
    size: str = "modal-xl",
    status_code: int = 200,
) -> HTMLResponse:
    """Return an HTMLResponse that renders a terminal console inside the admin modal.

    The console is a dark, monospace, auto-scrolling output area.  It supports
    static output, SSE streaming, and optional command input.

    Usage examples::

        # Static log output
        return console_response("Logs", log_text)

        # Streaming output via SSE
        return console_response("Deploy", "", stream_url="/deploy/stream")

        # Interactive shell
        return console_response("Shell", "Ready.\\n",
                                input_enabled=True, input_action="/shell/exec")

    Args:
        title: Console header title (auto-escaped).
        output: Initial text content for the console.  If *ansi* is True,
            ANSI escape codes are converted to coloured ``<span>`` elements.
        input_enabled: Show a command input prompt in the footer.
        input_action: ``hx-post`` URL for the input form (required when
            *input_enabled* is True).
        input_placeholder: Placeholder text for the input field.
        stream_url: SSE endpoint URL.  When set, new ``<pre>`` fragments are
            appended to the console output as they arrive.
        stream_event: SSE event name to listen for (default ``"output"``).
        ansi: Auto-convert ANSI escape codes in *output* (default True).
        size: Bootstrap modal size class (default ``"modal-xl"``).
        status_code: HTTP status code (default 200).
    """
    import html as html_mod

    safe_title = html_mod.escape(title)
    processed = ansi_to_html(output) if ansi else html_mod.escape(output)

    # Build the output area
    sse_attrs = ""
    if stream_url:
        sse_attrs = (
            f' hx-ext="sse" sse-connect="{html_mod.escape(stream_url)}"'
            f' sse-swap="{html_mod.escape(stream_event)}"'
            f' hx-swap="beforeend scroll:bottom"'
        )

    output_html = (
        f'<div class="console-output" id="console-output"{sse_attrs}>'
        f"<pre>{processed}</pre>"
        f"</div>"
    )

    # Build footer
    if input_enabled and input_action:
        safe_action = html_mod.escape(input_action)
        safe_placeholder = html_mod.escape(input_placeholder)
        footer_html = (
            f'<div class="modal-footer">'
            f'<form class="console-input-form" hx-post="{safe_action}"'
            f' hx-target="#console-output" hx-swap="beforeend scroll:bottom"'
            f' hx-on::after-request="this.reset()">'
            f'<input type="text" name="command" placeholder="{safe_placeholder}"'
            f' autocomplete="off" autofocus>'
            f'<button type="submit"><i class="bi bi-arrow-return-left"></i></button>'
            f"</form></div>"
        )
    else:
        footer_html = (
            '<div class="modal-footer">'
            '<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>'
            "</div>"
        )

    content = (
        f'<div class="console-modal">'
        f'<div class="modal-header">'
        f'<h5 class="modal-title" id="admin-modal-title">'
        f'<i class="bi bi-terminal"></i> {safe_title}</h5>'
        f'<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>'
        f"</div>"
        f'<div class="modal-body">{output_html}</div>'
        f"{footer_html}"
        f"</div>"
    )

    trigger_data: Dict[str, Any] = {}
    if size:
        trigger_data["size"] = size

    headers: Dict[str, str] = {
        "HX-Trigger": json.dumps({"showConsole": trigger_data}),
        "HX-Retarget": "#admin-modal .modal-content",
        "HX-Reswap": "innerHTML",
    }

    return HTMLResponse(content, status_code=status_code, headers=headers)


def console_sse_message(
    text: str,
    *,
    event: str = "output",
    ansi: bool = True,
    css_class: str = "",
) -> str:
    """Format a single SSE message for use with :func:`console_response` streaming.

    Use this inside a ``StreamingResponse`` generator::

        async def stream():
            yield console_sse_message("Starting...\\n", css_class="ansi-green")
            async for line in run_command():
                yield console_sse_message(line)
            yield console_sse_message("Done.\\n", css_class="ansi-green")

        return StreamingResponse(stream(), media_type="text/event-stream")

    Args:
        text: The text content for this message.
        event: SSE event name (must match *stream_event* in :func:`console_response`).
        ansi: Convert ANSI escape codes to HTML spans (default True).
        css_class: Optional CSS class(es) to add to the ``<pre>`` element.
    """
    import html as html_mod

    processed = ansi_to_html(text) if ansi else html_mod.escape(text)
    cls_attr = f' class="{html_mod.escape(css_class)}"' if css_class else ""
    html_fragment = f"<pre{cls_attr}>{processed}</pre>"

    # SSE format: each data line must be prefixed with "data: "
    # Multi-line data needs each line prefixed
    lines = html_fragment.split("\n")
    data_lines = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{data_lines}\n\n"


def celery_send_task(model, namespace:str, task_name:str, respond:bool=True):
    """ Helper for sending a Celery task.

    When respond=True (default), returns an HTMLResponse with a toast notification.
    Use this from endpoint handlers that return an HTTP response to the browser.

    When respond=False, sends the task and returns the AsyncResult directly.
    Use this from lifecycle hooks (on_model_change, after_model_change, etc.)
    where there is no HTTP response context.
    """

    if not Admin.celery_app:
        if respond:
            return toast_response("Celery app not configured", type="danger", status_code=500)
        raise RuntimeError("Celery app not configured")
    try:
        result = Admin.celery_app.send_task(f"{namespace.lower()}.{task_name}",
                                               args=[str(model.id)],
                                               queue=f"{namespace.lower()}")
        if respond:
            return toast_response(f"Task {result.id} Queued!", type="success")
        return result
    except Exception as e:
        if respond:
            return toast_response(f"Failed to send task: {str(e)}", type="danger", status_code=500)
        raise RuntimeError(
            f"Failed to send Celery task '{namespace}.{task_name}': {e}. "
            f"If this was called from a lifecycle hook, use respond=False. "
            f"If called from an endpoint, check Celery broker connectivity."
        ) from e


def _parse_filter_params(request: Request, column_filters, column_labels=None) -> list:
    """Parse filter parameters from query string.

    URL format: ``flt{index}_{column}_{operation}={value}``

    Returns a list of ``(column, operation, value, label)`` tuples where
    *label* is a human-readable column name derived from the key.
    """
    if not column_filters:
        return []
    allowed = set(column_filters)
    _labels = column_labels or {}
    indexed: list[tuple] = []
    for key, value in request.query_params.items():
        if not key.startswith("flt"):
            continue
        rest = key[3:]
        parts = rest.split("_", 1)
        if len(parts) < 2:
            continue
        if parts[0].isdigit():
            idx = int(parts[0])
            col_op = parts[1]
        else:
            idx = 0
            col_op = rest.lstrip("_")
        matched_col = None
        matched_op = None
        for col_name in sorted(allowed, key=len, reverse=True):
            if col_op.startswith(col_name + "_"):
                matched_col = col_name
                matched_op = col_op[len(col_name) + 1:]
                break
        if matched_col and matched_op:
            label = _labels.get(matched_col, matched_col.replace("_", " ").title())
            indexed.append((idx, matched_col, matched_op, value, label))
    indexed.sort(key=lambda x: x[0])
    return [(col, op, val, lbl) for _, col, op, val, lbl in indexed]


def _build_filter_defs(view, model, db: Session = None) -> list:
    """Build filter definitions for the template.

    Returns a list of dicts with keys: col, label, ops, choices (optional).
    When *choices* is present, the template should render a ``<select>``
    for the value field instead of a text ``<input>``.
    """
    if not view.column_filters:
        return []
    mapper = inspect(model)
    col_map = {c.key: c for c in mapper.columns}
    defs = []
    for col_key in view.column_filters:
        col_obj = col_map.get(col_key)
        if col_obj is None:
            continue
        col_type = type(col_obj.type).__name__
        label = view.column_labels.get(col_key, col_key.replace("_", " ").title()) if view.column_labels else col_key.replace("_", " ").title()

        # For FK columns: provide choices from the related table and simpler ops
        if col_key in view.foreign_keys and db is not None:
            fk_options = view._get_fk_options(db, col_key)
            choices = [[str(pk), display] for pk, display in fk_options]
            ops = [["equals", "equals"], ["not_equal", "not equal"], ["empty", "empty"]]
            defs.append({"col": col_key, "label": label, "ops": ops, "fk_choices": choices})
        else:
            ops = FILTER_TYPE_OPS.get(col_type, FILTER_OPS_STRING)
            defs.append({"col": col_key, "label": label, "ops": [list(o) for o in ops]})
    return defs


def _resolve_dotted(item, key):
    """Resolve a possibly dot-separated attribute path on *item*.

    For plain keys (no dot) this behaves exactly like ``getattr(item, key)``.
    For dotted keys like ``"palo1.hostname"`` it traverses the chain:
    ``item.palo1.hostname``, returning ``None`` if any intermediate is ``None``.
    """
    parts = key.split(".")
    obj = item
    for part in parts:
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def _safe_build_query(view, db, q, sort, order, active_filters):
    """Call ``view._build_query`` with filters, falling back if the subclass
    overrides ``_build_query`` without a *filters* parameter."""
    try:
        return view._build_query(db, search=q, sort=sort, order=order, filters=active_filters)
    except TypeError:
        # Subclass override doesn't accept 'filters' — call without it
        query = view._build_query(db, search=q, sort=sort, order=order)
        # Apply filters manually on the returned query
        if active_filters:
            for col_key, op, value, *_ in active_filters:
                col = getattr(view.model, col_key, None)
                if col is None:
                    continue
                mapper = inspect(view.model)
                ctype = {c.key: type(c.type).__name__ for c in mapper.columns}.get(col_key, "")
                if ctype in ("Integer",) and op != "empty":
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        continue
                if op == "equals":
                    query = query.filter(col == value)
                elif op == "not_equal":
                    query = query.filter(col != value)
                elif op == "contains":
                    query = query.filter(cast(col, String).ilike(f"%{value}%"))
                elif op == "not_contains":
                    query = query.filter(~cast(col, String).ilike(f"%{value}%"))
                elif op == "empty":
                    query = query.filter((col == None) | (cast(col, String) == ""))  # noqa: E711
                elif op == "greater":
                    query = query.filter(col > value)
                elif op == "smaller":
                    query = query.filter(col < value)
        return query


class CRUDView:
    """
    Given a SQLAlchemy model, generates list/detail/create/edit/delete routes.

    Subclass this and set class-level attributes to configure the view::

        class CustomerView(CRUDView):
            model = Customer
            column_list = ["id", "name", "sid"]
            form_sections = {"Basic": ["name", "sid"]}

    Then register via Admin::

        admin = Admin(app, templates)
        admin.add_view(CustomerView)
    """

    # --- Endpoint decorator ---

    @staticmethod
    def endpoint(
        path: str,
        methods: list[str] | None = None,
        *,
        audit: bool = False,
        audit_action: str | None = None,
        **route_kwargs,
    ):
        """Decorator for declaring custom endpoints on a CRUDView subclass.

        Usage::

            class MyView(CRUDView):
                model = MyModel

                @CRUDView.endpoint("/{name}/{item_id}/reset", methods=["POST"])
                def reset(self, request: Request, item_id: int, db: Session = Depends(get_db)):
                    ...

        ``{name}`` in the path is replaced with ``self.name`` at init time.
        The ``self`` parameter is bound automatically and hidden from FastAPI.

        Pass ``audit=True`` to emit an audit event after a successful return
        (requires view-level ``audit_log = True`` and ``Admin(audit_logger=...)``).
        Defaults: ``action = audit_action or fn.__name__``, ``item_id`` from the
        ``item_id`` path param if present, ``data = {path_params, query_params}``.
        Body data is not captured automatically — use ``self.audit(...)`` from
        inside the endpoint when you need richer payloads.
        """
        if methods is None:
            methods = ["GET"]

        def decorator(fn):
            fn._endpoint_meta = {
                "path": path,
                "methods": methods,
                "route_kwargs": route_kwargs,
                "audit": audit,
                "audit_action": audit_action,
            }
            return fn

        return decorator

    # --- Class-level config (override in subclasses) ---
    model = None
    name = None
    display_name = None
    category = None
    icon = None
    column_list = None
    column_export_list = None
    column_exclude = None
    column_labels = None
    column_formatters = None
    column_formatters_export = None
    column_searchable = None
    column_sortable = None
    form_columns = None
    form_sections = None
    form_widget_overrides = None
    form_ajax_refs = None
    row_actions = None
    multi_row_actions = None
    multi_row_select_all_pages = False
    page_size = 20
    pk_field = "id"
    can_create = True
    can_edit = True
    can_delete = True
    htmx_columns = None
    batch_poll_interval = 5
    column_filters = None
    column_header_filters = None
    detail_columns = None
    detail_columns_exclude = None
    export_types = None
    progress_redis_url = None
    list_template = "list.html"
    create_template = "form.html"
    edit_template = "form.html"
    allowed_users = None
    allowed_groups = None
    audit_log = False
    audit_log_exclude = None

    def __init__(self, templates):
        model = self.model
        if model is None:
            raise ValueError(f"{type(self).__name__} must define a 'model' attribute")

        self.templates = templates

        # Resolve defaults from model metadata where not set on the class
        if self.name is None:
            self.name = model.__tablename__
        if self.display_name is None:
            self.display_name = getattr(model, "__admin_name__", self.name.replace("_", " ").title())
        if self.category is None:
            self.category = getattr(model, "__admin_category__", None)
        if self.icon is None:
            self.icon = getattr(model, "__admin_icon__", "table")

        # Resolve mutable defaults (None -> empty collection)
        self.column_formatters = self.column_formatters or {}
        # Export formatters are opt-in only — unlike Flask-Admin, they do NOT
        # fall back to column_formatters, so exports stay raw unless explicitly set.
        self.column_formatters_export = self.column_formatters_export or {}
        self.column_labels = self.column_labels or {}
        self.form_widget_overrides = self.form_widget_overrides or {}
        self.form_ajax_refs = self.form_ajax_refs or {}
        self.row_actions = self.row_actions or []
        self.multi_row_actions = self.multi_row_actions or []
        self.htmx_columns = self.htmx_columns or {}

        # Register model in our registry
        _model_registry[model.__tablename__] = model

        # Introspect the model
        mapper = inspect(model)
        all_columns = [col.key for col in mapper.columns]
        self.relationships = {
            rel.key: rel for rel in mapper.relationships
        }
        self.foreign_keys = {}
        for col in mapper.columns:
            for fk in col.foreign_keys:
                self.foreign_keys[col.key] = fk
                # Register FK target models that may not have their own CRUDView
                target_table = fk.column.table
                if target_table.name not in _model_registry:
                    for rel in mapper.relationships:
                        rel_mapper = rel.mapper
                        if rel_mapper.local_table.name == target_table.name:
                            _model_registry[target_table.name] = rel_mapper.class_
                            break

        # Determine which columns to show in the list
        if self.column_list:
            pass  # already set on class
        elif self.column_exclude:
            self.column_list = [c for c in all_columns if c not in self.column_exclude]
        else:
            self.column_list = all_columns

        # Determine which columns to show in forms
        if not self.form_columns:
            self.form_columns = [
                c for c in all_columns
                if c != self.pk_field and c != "deploy_progress"
            ]

        # Build column metadata for templates (ordered by column_list)
        col_map_meta = {col_obj.key: col_obj for col_obj in mapper.columns}
        self.columns_meta = []
        for key in self.column_list:
            col_obj = col_map_meta.get(key)
            if col_obj is not None:
                col_type = type(col_obj.type).__name__
                self.columns_meta.append({
                    "key": col_obj.key,
                    "label": self.column_labels.get(col_obj.key, col_obj.key.replace("_", " ").title()),
                    "type": col_type,
                    "sortable": self.column_sortable is None or col_obj.key in (self.column_sortable or []),
                })
            elif "." in key:
                # Dot-notation: e.g. "palo1.hostname" → traverse relationship
                parts = key.split(".")
                rel_key = parts[0]
                rel = self.relationships.get(rel_key)
                if rel is not None:
                    # Walk the chain to resolve the final column type
                    related_mapper = rel.mapper
                    col_type = "String"
                    for attr in parts[1:]:
                        rel_col = related_mapper.columns.get(attr)
                        if rel_col is not None:
                            col_type = type(rel_col.type).__name__
                        else:
                            # Could be a nested relationship; try to keep walking
                            nested_rel = {r.key: r for r in related_mapper.relationships}.get(attr)
                            if nested_rel is not None:
                                related_mapper = nested_rel.mapper
                            else:
                                break
                    default_label = key.replace(".", " ").replace("_", " ").title()
                    self.columns_meta.append({
                        "key": key,
                        "label": self.column_labels.get(key, self.column_labels.get(rel_key, default_label)),
                        "type": col_type,
                        "sortable": False,  # relationship traversal not sortable at DB level
                    })

        # Build export column metadata. Defaults to the list-view columns, but
        # column_export_list overrides for exports only — it may include or reorder
        # columns that are not shown in the list view (Flask-Admin parity).
        if self.column_export_list:
            self.export_columns_meta = []
            for key in self.column_export_list:
                rel_key = key.split(".")[0]
                default_label = key.replace(".", " ").replace("_", " ").title()
                label = self.column_labels.get(
                    key, self.column_labels.get(rel_key, default_label)
                )
                self.export_columns_meta.append({"key": key, "label": label})
        else:
            self.export_columns_meta = self.columns_meta

        # Map each displayed column to the header-filter keys that render under it.
        # A dotted filter keyed by a FK column (e.g. "serverid.status") is resolved
        # to its relationship display column ("server.status") when that column is
        # shown, so multiple FK-prefixed filters don't collide on the FK column and
        # silently drop. The original filter key is preserved as the cf_ param name.
        self.header_filter_map = {}
        if self.column_header_filters:
            col_keys_set = {c["key"] for c in self.columns_meta}
            fk_to_rel = {}
            for rel_name, rel in self.relationships.items():
                for local_col in rel.local_columns:
                    fk_to_rel[local_col.key] = rel_name
            for hf in self.column_header_filters:
                target = None
                if hf in col_keys_set:
                    target = hf
                elif "." in hf:
                    prefix, rest = hf.split(".", 1)
                    resolved = f"{fk_to_rel[prefix]}.{rest}" if prefix in fk_to_rel else None
                    if resolved and resolved in col_keys_set:
                        target = resolved
                    elif prefix in col_keys_set:
                        target = prefix
                    else:
                        for ck in col_keys_set:
                            if hf.startswith(ck + "."):
                                target = ck
                                break
                if target is not None:
                    self.header_filter_map.setdefault(target, []).append(hf)

        # Build detail view metadata (all columns by default, or detail_columns if set)
        detail_keys = self.detail_columns or all_columns
        if self.detail_columns_exclude:
            detail_keys = [k for k in detail_keys if k not in self.detail_columns_exclude]
        self.detail_meta = []
        for key in detail_keys:
            col_obj = col_map_meta.get(key)
            if col_obj is not None:
                col_type = type(col_obj.type).__name__
                self.detail_meta.append({
                    "key": col_obj.key,
                    "label": (self.column_labels or {}).get(col_obj.key, col_obj.key.replace("_", " ").title()),
                    "type": col_type,
                })

        # Build form field metadata (ordered by form_columns)
        self.form_fields = []
        col_map = {col_obj.key: col_obj for col_obj in mapper.columns}
        for col_key in self.form_columns:
            col_obj = col_map.get(col_key)
            if col_obj is not None:
                col_type = type(col_obj.type).__name__
                html_type = COLUMN_TYPE_MAP.get(col_type, "text")

                # Check if this is an enum column
                choices = None
                if hasattr(col_obj.type, "enum_class") and col_obj.type.enum_class:
                    choices = [(e.value, e.value.title()) for e in col_obj.type.enum_class]
                    html_type = "select"

                # Check if this is a foreign key
                if col_obj.key in self.foreign_keys:
                    if self.form_ajax_refs and col_obj.key in self.form_ajax_refs:
                        html_type = "ajax_select"
                    else:
                        html_type = "select"

                field = {
                    "key": col_obj.key,
                    "label": self.column_labels.get(col_obj.key, col_obj.key.replace("_", " ").title()),
                    "type": html_type,
                    "required": not col_obj.nullable and col_obj.default is None,
                    "choices": choices,
                    "is_fk": col_obj.key in self.foreign_keys,
                }
                field.update(self.form_widget_overrides.get(col_obj.key, {}))
                self.form_fields.append(field)
            elif col_key in self.form_widget_overrides:
                # Virtual field: not a model column, defined entirely via form_widget_overrides
                override = self.form_widget_overrides[col_key]
                field = {
                    "key": col_key,
                    "label": override.get("label", col_key.replace("_", " ").title()),
                    "type": override.get("type", "text"),
                    "required": override.get("required", False),
                    "choices": override.get("choices"),
                    "is_fk": False,
                    "virtual": True,
                }
                field.update(override)
                self.form_fields.append(field)

        # Build searchable columns
        if self.column_searchable is None:
            self.column_searchable = [
                col.key for col in mapper.columns
                if isinstance(col.type, String)
            ]

        self.router = APIRouter()
        self._setup_htmx_polling_routes()
        self._setup_batch_poll_route()
        self._setup_progress_route()
        self._setup_ajax_select_routes()
        self._setup_select_all_ids_route()
        self._setup_decorated_endpoints()
        self.setup_endpoints()
        self._setup_routes()

    def _setup_ajax_select_routes(self):
        """Register HTMX search endpoints for form_ajax_refs fields."""
        if not self.form_ajax_refs:
            return

        view = self

        for field_key, config in self.form_ajax_refs.items():
            target_model = config["model"]
            search_fields = config.get("fields", [])
            page_size = config.get("page_size", 10)
            # Optional result-set constraints:
            #   "filters": list of SQLAlchemy expressions applied to every query
            #   "query_filter": callable (query, db, request) -> query, for dynamic/request-aware filtering
            static_filters = config.get("filters", [])
            query_filter = config.get("query_filter")

            def make_handler(fk, tgt_model, s_fields, p_size, base_filters, q_filter):
                def search_handler(
                    request: Request,
                    q: str = "",
                    page: int = 1,
                    db: Session = Depends(get_db),
                ):
                    denied = view._check_access(request)
                    if denied:
                        return denied
                    if page < 1:
                        page = 1
                    query = db.query(tgt_model)
                    for expr in base_filters:
                        query = query.filter(expr)
                    if q_filter is not None:
                        query = q_filter(query, db, request)
                    if q and s_fields:
                        filters = [
                            getattr(tgt_model, f).ilike(f"%{q}%")
                            for f in s_fields
                            if hasattr(tgt_model, f)
                        ]
                        if filters:
                            query = query.filter(or_(*filters))

                    rows = query.offset((page - 1) * p_size).limit(p_size + 1).all()
                    has_more = len(rows) > p_size
                    rows = rows[:p_size]

                    results = [
                        {"value": str(getattr(item, "id", "")), "label": str(item)}
                        for item in rows
                    ]

                    next_url = None
                    if has_more:
                        next_url = (
                            f"{request.url.path}?"
                            + urlencode({"q": q, "page": page + 1})
                        )

                    return {"items": results, "more": has_more, "next": next_url}

                search_handler.__name__ = f"{view.name}_{fk}_ajax_search"
                return search_handler

            handler = make_handler(field_key, target_model, search_fields, page_size, static_filters, query_filter)
            self.router.add_api_route(
                f"/{self.name}/ajax/{field_key}",
                handler,
                methods=["GET"],
            )

    def _get_fk_options(self, db: Session, field_key: str) -> list:
        """Get options for a foreign key select field."""
        fk = self.foreign_keys.get(field_key)
        if not fk:
            return []
        target_table = fk.column.table
        target_model = _model_registry.get(target_table.name)
        if target_model:
            items = db.query(target_model).all()
            return [(getattr(item, 'id', str(item)), str(item)) for item in items]
        return []

    def _build_query(self, db: Session, search: str = "", sort: str = "", order: str = "asc", filters: list | None = None):
        """Build a query with search, sorting, and column filters.

        ``filters`` is a list of (col_key, operation, value) tuples.
        """
        query = db.query(self.model)

        # Apply column filters
        if filters:
            mapper = inspect(self.model)
            col_type_map = {c.key: type(c.type).__name__ for c in mapper.columns}
            for col_key, op, value, *_ in filters:
                col = getattr(self.model, col_key, None)
                if col is None:
                    continue
                # Cast value for numeric columns
                ctype = col_type_map.get(col_key, "")
                if ctype in ("Integer",) and op != "empty":
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        continue  # skip invalid numeric filter
                elif ctype in ("Float",) and op != "empty":
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        continue
                if op == "contains":
                    query = query.filter(cast(col, String).ilike(f"%{value}%"))
                elif op == "not_contains":
                    query = query.filter(~cast(col, String).ilike(f"%{value}%"))
                elif op == "equals":
                    query = query.filter(col == value)
                elif op == "not_equal":
                    query = query.filter(col != value)
                elif op == "empty":
                    query = query.filter((col == None) | (cast(col, String) == ""))  # noqa: E711
                elif op == "greater":
                    query = query.filter(col > value)
                elif op == "smaller":
                    query = query.filter(col < value)
                elif op == "in_list":
                    vals = [v.strip() for v in str(value).split(",") if v.strip()]
                    if vals:
                        query = query.filter(col.in_(vals))

        if search and self.column_searchable:
            mapper = inspect(self.model)
            search_filters = []
            joined_tables = set()
            for col_key in self.column_searchable:
                # Dotted notation: "serverid.hostname" targets a specific FK column
                if "." in col_key:
                    fk_col, target_col_name = col_key.split(".", 1)
                    if fk_col in self.foreign_keys:
                        fk = self.foreign_keys[fk_col]
                        target_table = fk.column.table
                        target_model = _model_registry.get(target_table.name)
                        if target_model:
                            if target_table.name not in joined_tables:
                                local_col = mapper.columns[fk_col]
                                query = query.outerjoin(target_model, local_col == fk.column)
                                joined_tables.add(target_table.name)
                            tcol = getattr(target_model, target_col_name, None)
                            if tcol is not None:
                                search_filters.append(cast(tcol, String).ilike(f"%{search}%"))
                    continue
                col = mapper.columns[col_key]
                # Bare FK column: join and search all string columns on the related table
                if col_key in self.foreign_keys:
                    fk = self.foreign_keys[col_key]
                    target_table = fk.column.table
                    target_model = _model_registry.get(target_table.name)
                    if target_model:
                        if target_table.name not in joined_tables:
                            query = query.outerjoin(target_model, col == fk.column)
                            joined_tables.add(target_table.name)
                        target_mapper = inspect(target_model)
                        for tcol in target_mapper.columns:
                            if isinstance(tcol.type, String):
                                search_filters.append(cast(tcol, String).ilike(f"%{search}%"))
                else:
                    # Always cast to String: some declared String columns map to
                    # Postgres types without a text ILIKE operator (e.g. cidr/inet),
                    # so trusting the declared type is unsafe. Casting a real varchar
                    # is effectively a no-op for a leading-% search (no index use anyway).
                    search_filters.append(cast(col, String).ilike(f"%{search}%"))
            if search_filters:
                query = query.filter(or_(*search_filters))

        if sort:
            mapper = inspect(self.model)
            if sort in [c.key for c in mapper.columns]:
                col = getattr(self.model, sort)
                query = query.order_by(col.desc() if order == "desc" else col.asc())
        else:
            query = query.order_by(getattr(self.model, self.pk_field).desc())

        return query

    def get_colspan(self) -> int:
        """Calculate table colspan (columns + actions column if present)."""
        show_actions = self.row_actions or self.can_edit or self.can_delete
        return len(self.columns_meta) + (1 if show_actions else 0)

    def _active_progress(self, item_ids) -> Dict[Any, Dict[str, Any]]:
        """Return ``{item_id: {"progress": int, "status": str}}`` for rows that
        have an in-progress (non-terminal) progress bar stored in Redis.

        Used to re-hydrate progress bars on a full page load / table refresh —
        without this, a bar inserted by ``progress_response`` vanishes the moment
        the page is reloaded, because the initial render only reads model values.

        Only genuinely in-flight bars (0 <= progress < 100, not "Error") are
        returned; terminal states are skipped since the column value already
        reflects the final outcome and polling has stopped.
        """
        if not self.progress_redis_url or not item_ids:
            return {}
        if not any(a.get("progress") for a in self.row_actions):
            return {}
        try:
            import redis as redis_lib
            r = redis_lib.Redis.from_url(self.progress_redis_url, decode_responses=True)
            raws = r.mget([f"{self.name}:{i}:progress" for i in item_ids])
        except Exception:
            return {}
        active: Dict[Any, Dict[str, Any]] = {}
        for item_id, raw in zip(item_ids, raws):
            if raw is None or raw == "Error":
                continue
            try:
                val = int(raw)
            except (ValueError, TypeError):
                continue
            if val >= 100:
                continue
            active[item_id] = {"progress": val, "status": "Deploying..."}
        return active

    def progress_response(self, request: Request, item_id, item=None, progress: int = 0, status: str = "Starting..."):
        """Return an HTMLResponse rendering the progress bar row.

        Call this from your action endpoint to insert the initial progress bar.
        The auto-registered progress polling endpoint handles subsequent updates.

        If ``item`` is provided and ``htmx_columns`` are configured, OOB swaps
        are automatically appended to restart polling on those columns.
        """
        progress_html = self.templates.TemplateResponse("partials/progress_bar.html", {
            "request": request,
            "view_name": self.name,
            "item_id": item_id,
            "progress": progress,
            "status": status,
            "colspan": self.get_colspan(),
        }).body.decode()

        # Auto-generate OOB swaps for any htmx_columns to restart their polling
        if item is not None and self.htmx_columns:
            formatters = self.column_formatters or {}
            for col_key, config in self.htmx_columns.items():
                value = getattr(item, col_key, None)
                fmt = formatters.get(col_key)
                content = fmt(value, item) if fmt else (value.value if hasattr(value, "value") else str(value or ""))
                url = config["url"].replace("{id}", str(item_id))
                trigger = config.get("trigger", "every 3s")
                progress_html += (
                    f'<span id="htmx-{col_key}-{item_id}"'
                    f' hx-get="{url}"'
                    f' hx-trigger="load, {trigger}"'
                    f' hx-swap="innerHTML"'
                    f' hx-swap-oob="true">'
                    f'{content}</span>'
                )

        return HTMLResponse(progress_html)

    def _setup_progress_route(self):
        """Auto-register GET /{name}/{item_id}/progress when any row_action has progress=True."""
        has_progress = any(a.get("progress") for a in self.row_actions)
        if not has_progress or not self.progress_redis_url:
            return

        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError(
                "redis is required for progress bar support. "
                "Install it with: pip install redis"
            )

        view = self
        redis_url = self.progress_redis_url

        def progress_handler(request: Request, item_id):
            denied = view._check_access(request)
            if denied:
                return denied
            try:
                r = redis_lib.Redis.from_url(redis_url, decode_responses=True)
                raw = r.get(f"{view.name}:{item_id}:progress")
            except Exception:
                raw = None

            if raw is None:
                progress_val = 0
                status = "Waiting..."
            elif raw == "Error":
                progress_val = 100
                status = "Error"
            else:
                try:
                    progress_val = int(raw)
                except (ValueError, TypeError):
                    progress_val = 0
                status = "Complete" if progress_val >= 100 else "Deploying..."

            return view.templates.TemplateResponse("partials/progress_bar.html", {
                "request": request,
                "view_name": view.name,
                "item_id": item_id,
                "progress": progress_val,
                "status": status,
                "colspan": view.get_colspan(),
            })

        progress_handler.__name__ = f"{self.name}_progress_poll"
        self.router.add_api_route(
            f"/{self.name}/{{item_id}}/progress",
            progress_handler,
            methods=["GET"],
            response_class=HTMLResponse,
        )

    def render_row(self, request, item):
        """Render a single <tr> for the given item via the table_body partial."""
        row = {"_obj": item, "_id": getattr(item, self.pk_field), "cells": {}}
        for col_meta in self.columns_meta:
            key = col_meta["key"]
            value = _resolve_dotted(item, key)
            formatted = self.column_formatters[key](value, item) if key in self.column_formatters else value
            row["cells"][key] = {
                "raw": value,
                "formatted": formatted,
                "htmx": self.htmx_columns.get(key),
            }
        return self.templates.TemplateResponse("partials/table_body.html", {
            "request": request,
            "view": self,
            "rows": [row],
            "columns": self.columns_meta,
            "row_actions": self.row_actions,
        })

    def _setup_htmx_polling_routes(self):
        """Auto-register GET endpoints for each htmx_columns entry."""
        if not self.htmx_columns:
            return

        model = self.model
        templates = self.templates
        view = self
        formatters = self.column_formatters or {}

        for field_key, config in self.htmx_columns.items():
            # Convert URL pattern: /edges/{id}/status -> /edges/{item_id}/status
            url = config["url"].replace("{id}", "{item_id}")
            formatter = formatters.get(field_key)
            terminal_states = config.get("terminal_states", [])

            def make_handler(fk, fmt, terminals):
                def handler(request: Request, item_id, db: Session = Depends(get_db)):
                    denied = view._check_access(request)
                    if denied:
                        return denied
                    item = db.query(model).filter(getattr(model, view.pk_field) == item_id).first()
                    if not item:
                        return HTMLResponse("")
                    value = getattr(item, fk)
                    # Resolve enum values for comparison
                    status_str = value.value if hasattr(value, "value") else str(value)
                    # HTTP 286 tells HTMX to stop polling (case-insensitive match)
                    status_code = 286 if terminals and status_str.lower() in [t.lower() for t in terminals] else 200
                    if fmt:
                        return HTMLResponse(fmt(value, item), status_code=status_code)
                    return HTMLResponse(status_str, status_code=status_code)
                handler.__name__ = f"{view.name}_{fk}_poll"
                return handler

            self.router.add_api_route(
                url,
                make_handler(field_key, formatter, terminal_states),
                methods=["GET"],
                response_class=HTMLResponse,
            )

    def _setup_select_all_ids_route(self):
        """Register GET /{name}/select-all-ids — returns JSON list of every
        primary-key id that matches the current filter/search/header-filter
        state. Only mounted when multi_row_select_all_pages is enabled."""
        if not self.multi_row_select_all_pages or not self.multi_row_actions:
            return

        model = self.model
        view = self

        def select_all_ids(request: Request, q: str = "", sort: str = "", order: str = "asc", db: Session = Depends(get_db)):
            denied = view._check_access(request)
            if denied:
                return denied
            active_filters = _parse_filter_params(request, view.column_filters, view.column_labels)

            header_filter_values = {}
            if view.column_header_filters:
                allowed_hf = set(view.column_header_filters)
                for key, val in request.query_params.items():
                    if key.startswith("cf_") and val:
                        col_key = key[3:]
                        if col_key in allowed_hf:
                            header_filter_values[col_key] = val

            query = _safe_build_query(view, db, q, sort, order, active_filters)

            if header_filter_values:
                mapper = inspect(model)
                for col_key, val in header_filter_values.items():
                    if "." in col_key:
                        fk_col, target_col_name = col_key.split(".", 1)
                        if fk_col in view.foreign_keys:
                            fk = view.foreign_keys[fk_col]
                            target_table = fk.column.table
                            target_model = _model_registry.get(target_table.name)
                            if target_model:
                                if target_table.name not in _joined_table_names(query):
                                    local_col = mapper.columns[fk_col]
                                    query = query.outerjoin(target_model, local_col == fk.column)
                                tcol = getattr(target_model, target_col_name, None)
                                if tcol is not None:
                                    query = query.filter(cast(tcol, String).ilike(f"%{val.strip()}%"))
                    else:
                        col = getattr(model, col_key, None)
                        if col is not None:
                            query = query.filter(cast(col, String).ilike(f"%{val.strip()}%"))

            pk_col = getattr(model, view.pk_field)
            # Cap bulk selection at MULTI_ROW_SELECT_LIMIT to stay within the
            # multi_row_actions wire contract (single comma-joined `ids` field).
            id_query = query.with_entities(pk_col)
            total = id_query.count()
            ids = [row[0] for row in id_query.limit(MULTI_ROW_SELECT_LIMIT).all()]
            truncated = total > len(ids)
            return JSONResponse({
                "ids": ids,
                "total": total,
                "returned": len(ids),
                "truncated": truncated,
                "limit": MULTI_ROW_SELECT_LIMIT,
            })

        select_all_ids.__name__ = f"{self.name}_select_all_ids"
        self.router.add_api_route(
            f"/{self.name}/select-all-ids",
            select_all_ids,
            methods=["GET"],
        )

    def _setup_batch_poll_route(self):
        """Auto-register GET /{name}/poll-status for batch htmx column polling."""
        if not self.htmx_columns:
            return

        model = self.model
        view = self
        formatters = self.column_formatters or {}

        def batch_poll(request: Request, ids: str = "", db: Session = Depends(get_db)):
            denied = view._check_access(request)
            if denied:
                return denied
            if not ids:
                return JSONResponse({})
            id_list = [i.strip() for i in ids.split(",") if i.strip()]
            if not id_list:
                return JSONResponse({})
            items = db.query(model).filter(getattr(model, view.pk_field).in_(id_list)).all()
            result = {}
            for item in items:
                item_id = str(getattr(item, view.pk_field))
                result[item_id] = {}
                for field_key, config in view.htmx_columns.items():
                    value = getattr(item, field_key)
                    status_str = value.value if hasattr(value, "value") else str(value)
                    terminals = config.get("terminal_states", [])
                    terminal = bool(terminals and status_str.lower() in [t.lower() for t in terminals])
                    fmt = formatters.get(field_key)
                    html = fmt(value, item) if fmt else status_str
                    result[item_id][field_key] = {"html": html, "terminal": terminal}
            return JSONResponse(result)

        batch_poll.__name__ = f"{self.name}_batch_poll"
        self.router.add_api_route(
            f"/{self.name}/poll-status",
            batch_poll,
            methods=["GET"],
        )

    def _setup_decorated_endpoints(self):
        """Collect methods decorated with @CRUDView.endpoint and register them."""
        import typing

        for attr_name in dir(type(self)):
            fn = getattr(type(self), attr_name, None)
            if fn is None or not callable(fn) or not hasattr(fn, "_endpoint_meta"):
                continue

            meta = fn._endpoint_meta
            path = meta["path"].replace("{name}", self.name).replace("{prefix}", f"/{self.name}")

            bound = fn.__get__(self, type(self))

            # Resolve string annotations (from `from __future__ import annotations`)
            # back to real types so FastAPI can process Depends(), Request, etc.
            hints = typing.get_type_hints(fn, include_extras=True)

            sig = signature(fn)
            params = []
            for pname, p in sig.parameters.items():
                if pname == "self":
                    continue
                annotation = hints.get(pname, p.annotation)
                params.append(p.replace(annotation=annotation))
            new_sig = sig.replace(
                parameters=params,
                return_annotation=hints.get("return", sig.return_annotation),
            )

            audit_enabled = bool(meta.get("audit"))
            audit_action_name = meta.get("audit_action") or fn.__name__

            @functools.wraps(fn)
            async def make_handler(
                bound_method=bound,
                _audit=audit_enabled,
                _audit_action=audit_action_name,
                _view=self,
                **kwargs,
            ):
                result = await bound_method(**kwargs)
                if _audit:
                    request = kwargs.get("request")
                    item_id = kwargs.get("item_id")
                    data = {}
                    if request is not None:
                        path_params = dict(getattr(request, "path_params", {}) or {})
                        query_params = dict(getattr(request, "query_params", {}) or {})
                        if path_params:
                            data["path_params"] = path_params
                        if query_params:
                            data["query_params"] = query_params
                    _view._audit_emit(
                        action=_audit_action,
                        item=None,
                        item_id=item_id,
                        request=request,
                        data=data,
                    )
                return result

            make_handler.__signature__ = new_sig

            self.router.add_api_route(
                path,
                make_handler,
                methods=meta["methods"],
                **meta["route_kwargs"],
            )

    def setup_endpoints(self):
        """Override in subclasses to register custom HTMX endpoints on self.router."""
        pass

    def _check_access(self, request: Request):
        """Return a 403 response if the current user is not allowed, else None."""
        if not self.allowed_users and not self.allowed_groups:
            return None
        user = get_current_user(request)
        username = (user or {}).get("username", "")
        user_groups = (user or {}).get("groups", [])
        if self.allowed_users and username in self.allowed_users:
            return None
        if self.allowed_groups and any(g in self.allowed_groups for g in user_groups):
            return None
        return HTMLResponse("Access denied", status_code=403)

    def _setup_routes(self):
        model = self.model
        templates = self.templates
        view = self

        @self.router.get(f"/{self.name}", response_class=HTMLResponse)
        def list_view(
            request: Request,
            page: int = 1,
            q: str = "",
            sort: str = "",
            order: str = "asc",
            db: Session = Depends(get_db),
        ):
            denied = view._check_access(request)
            if denied:
                return denied
            # Parse active filters from query params (flt{idx}_{col}_{op}=value)
            active_filters = _parse_filter_params(request, view.column_filters, view.column_labels)

            # Parse inline header filters (cf_{col}=value)
            header_filter_values = {}
            if view.column_header_filters:
                allowed_hf = set(view.column_header_filters)
                for key, val in request.query_params.items():
                    if key.startswith("cf_") and val:
                        col_key = key[3:]
                        if col_key in allowed_hf:
                            header_filter_values[col_key] = val

            query = _safe_build_query(view, db, q, sort, order, active_filters)

            # Apply header filters as "contains" on each column
            if header_filter_values:
                mapper = inspect(model)
                for col_key, val in header_filter_values.items():
                    # Dotted notation: "serverid.hostname" targets a specific FK column
                    if "." in col_key:
                        fk_col, target_col_name = col_key.split(".", 1)
                        if fk_col in view.foreign_keys:
                            fk = view.foreign_keys[fk_col]
                            target_table = fk.column.table
                            target_model = _model_registry.get(target_table.name)
                            if target_model:
                                # Skip the join if the search path (or a subclass
                                # _build_query) already joined this table, else
                                # Postgres raises DuplicateAlias.
                                if target_table.name not in _joined_table_names(query):
                                    local_col = mapper.columns[fk_col]
                                    query = query.outerjoin(target_model, local_col == fk.column)
                                tcol = getattr(target_model, target_col_name, None)
                                if tcol is not None:
                                    query = query.filter(cast(tcol, String).ilike(f"%{val.strip()}%"))
                    else:
                        col = getattr(model, col_key, None)
                        if col is not None:
                            query = query.filter(cast(col, String).ilike(f"%{val.strip()}%"))
            total = query.count()
            total_pages = max(1, math.ceil(total / view.page_size))
            page = max(1, min(page, total_pages))
            items = query.offset((page - 1) * view.page_size).limit(view.page_size).all()

            rows = []
            for item in items:
                row = {"_obj": item, "_id": getattr(item, view.pk_field), "cells": {}}
                for col_meta in view.columns_meta:
                    key = col_meta["key"]
                    value = _resolve_dotted(item, key)
                    if key in view.column_formatters:
                        formatted = view.column_formatters[key](value, item)
                    else:
                        formatted = value
                    row["cells"][key] = {
                        "raw": value,
                        "formatted": formatted,
                        "htmx": view.htmx_columns.get(key),
                    }
                rows.append(row)

            # Re-hydrate any in-flight progress bars from Redis so they survive a
            # full page load / table refresh (not just the initial afterend insert).
            active_progress = view._active_progress([row["_id"] for row in rows])
            if active_progress:
                for row in rows:
                    row["progress"] = active_progress.get(row["_id"])

            # Build filter definitions for the template
            filter_defs = _build_filter_defs(view, model, db)

            context = {
                "request": request,
                "view": view,
                "rows": rows,
                "columns": view.columns_meta,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "search": q,
                "sort": sort,
                "order": order,
                "row_actions": view.row_actions,
                "filter_defs": filter_defs,
                "active_filters": active_filters,
                "header_filter_values": header_filter_values,
                "progress_colspan": view.get_colspan(),
            }

            if request.headers.get("HX-Request") and request.query_params.get("partial"):
                response = templates.TemplateResponse("partials/table_body.html", context)
                # Build a clean URL (without partial=1) so the browser URL
                # reflects the current search/sort/filter state and survives reload.
                params = []
                if q:
                    params.append(("q", q))
                if sort:
                    params.append(("sort", sort))
                if order and order != "asc":
                    params.append(("order", order))
                if page > 1:
                    params.append(("page", str(page)))
                for key, val in request.query_params.multi_items():
                    if key.startswith("flt") or key.startswith("cf_"):
                        params.append((key, val))
                clean_url = f"/{view.name}"
                if params:
                    clean_url += "?" + urlencode(params)
                response.headers["HX-Replace-Url"] = clean_url
                response.headers["HX-Trigger"] = json.dumps({
                    "listMetaUpdate": {
                        "total_pages": total_pages,
                        "page": page,
                        "total": total,
                    }
                })
                return response

            return templates.TemplateResponse(view.list_template, context)

        if view.export_types:
            @self.router.get(f"/{self.name}/export/{{fmt}}")
            def export_view(
                request: Request,
                fmt: str,
                q: str = "",
                sort: str = "",
                order: str = "asc",
                db: Session = Depends(get_db),
            ):
                denied = view._check_access(request)
                if denied:
                    return denied
                if fmt not in view.export_types:
                    return HTMLResponse("Export format not supported", status_code=400)

                active_filters = _parse_filter_params(request, view.column_filters, view.column_labels)

                query = _safe_build_query(view, db, q, sort, order, active_filters)
                items = query.all()

                # Get column keys and labels
                col_keys = [c["key"] for c in view.export_columns_meta]
                col_labels = [c["label"] for c in view.export_columns_meta]

                # Build rows of raw values
                rows_data = []
                export_formatters = view.column_formatters_export
                for item in items:
                    row = []
                    for key in col_keys:
                        value = _resolve_dotted(item, key)
                        formatter = export_formatters.get(key)
                        if formatter is not None:
                            value = formatter(value, item)
                        elif hasattr(value, "value"):
                            value = value.value
                        if value is None:
                            value = ""
                        row.append(str(value))
                    rows_data.append(row)

                if fmt == "csv":
                    output = io.StringIO()
                    writer = csv.writer(output)
                    writer.writerow(col_labels)
                    writer.writerows(rows_data)
                    return StreamingResponse(
                        iter([output.getvalue()]),
                        media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={view.name}.csv"},
                    )

                if fmt == "xlsx":
                    try:
                        import openpyxl
                    except ImportError:
                        return HTMLResponse(
                            "openpyxl is required for XLSX export: pip install openpyxl",
                            status_code=500,
                        )
                    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

                    def _xlsx_safe(v):
                        # XLSX forbids ASCII control chars in cell text; strip
                        # them so a stray control char doesn't 500 the export.
                        return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v

                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = view.display_name
                    ws.append([_xlsx_safe(c) for c in col_labels])
                    for row in rows_data:
                        ws.append([_xlsx_safe(c) for c in row])
                    output = io.BytesIO()
                    wb.save(output)
                    output.seek(0)
                    return StreamingResponse(
                        output,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={view.name}.xlsx"},
                    )

                return HTMLResponse("Unsupported format", status_code=400)

        @self.router.get(f"/{self.name}/create", response_class=HTMLResponse)
        def create_form(
            request: Request,
            db: Session = Depends(get_db),
        ):
            denied = view._check_access(request)
            if denied:
                return denied
            if not view.can_create:
                return HTMLResponse("Create not allowed", status_code=403)

            form_fields = view._prepare_form_fields(db)

            return templates.TemplateResponse(view.create_template, {
                "request": request,
                "view": view,
                "form_fields": form_fields,
                "form_sections": view.form_sections,
                "item": None,
                "action": f"/{view.name}/create",
                "title": f"Create {view.display_name}",
            })

        @self.router.post(f"/{self.name}/create", response_class=HTMLResponse)
        async def create_submit(
            request: Request,
            db: Session = Depends(get_db),
        ):
            denied = view._check_access(request)
            if denied:
                return denied
            form_data = await request.form()
            item = model()
            try:
                view._apply_form_data(item, form_data)
                view.on_model_change(item, form_data, is_new=True, db=db, request=request)
                db.add(item)
                db.commit()
            except ValidationError as e:
                db.rollback()
                error_msg = e.message
            except IntegrityError as e:
                db.rollback()
                error_msg = "A required field is missing or a value already exists."
            except Exception as e:
                db.rollback()
                error_msg = str(e) or "An unexpected error occurred."
            else:
                error_msg = None
                view.after_model_change(item, form_data, is_new=True, db=db, request=request)
                view._audit_emit(
                    action="create",
                    item=item,
                    request=request,
                    data=view._audit_snapshot(item),
                )
            if error_msg:
                form_fields = view._prepare_form_fields(db, item)
                return templates.TemplateResponse(view.create_template, {
                    "request": request,
                    "view": view,
                    "form_fields": form_fields,
                    "form_sections": view.form_sections,
                    "item": None,
                    "action": f"/{view.name}/create",
                    "title": f"Create {view.display_name}",
                }, headers={
                    "HX-Trigger": json.dumps({"showToast": {
                        "message": error_msg, "type": "danger", "title": "Validation Error",
                    }}),
                })
            if request.headers.get("HX-Request"):
                return HTMLResponse("", headers={"HX-Redirect": f"/{view.name}"})
            return RedirectResponse(f"/{view.name}", status_code=303)

        @self.router.get(f"/{self.name}/{{item_id}}", response_class=HTMLResponse)
        def detail_view(
            request: Request,
            item_id,
            db: Session = Depends(get_db),
        ):
            denied = view._check_access(request)
            if denied:
                return denied
            item = db.query(model).filter(getattr(model, view.pk_field) == item_id).first()
            if not item:
                return HTMLResponse("Not found", status_code=404)

            fields = []
            for col_meta in view.detail_meta:
                key = col_meta["key"]
                value = _resolve_dotted(item, key)
                if key in (view.column_formatters or {}):
                    formatted = view.column_formatters[key](value, item)
                else:
                    formatted = value
                fields.append({
                    "label": col_meta["label"],
                    "value": formatted,
                    "raw": value,
                })

            return templates.TemplateResponse("detail.html", {
                "request": request,
                "view": view,
                "item": item,
                "fields": fields,
            })

        @self.router.get(f"/{self.name}/{{item_id}}/edit", response_class=HTMLResponse)
        def edit_form(
            request: Request,
            item_id,
            db: Session = Depends(get_db),
        ):
            denied = view._check_access(request)
            if denied:
                return denied
            if not view.can_edit:
                return HTMLResponse("Edit not allowed", status_code=403)

            item = db.query(model).filter(getattr(model, view.pk_field) == item_id).first()
            if not item:
                return HTMLResponse("Not found", status_code=404)

            form_fields = view._prepare_form_fields(db, item)

            return templates.TemplateResponse(view.edit_template, {
                "request": request,
                "view": view,
                "form_fields": form_fields,
                "form_sections": view.form_sections,
                "item": item,
                "action": f"/{view.name}/{item_id}/edit",
                "title": f"Edit {view.display_name}",
            })

        @self.router.post(f"/{self.name}/{{item_id}}/edit", response_class=HTMLResponse)
        async def edit_submit(
            request: Request,
            item_id,
            db: Session = Depends(get_db),
        ):
            denied = view._check_access(request)
            if denied:
                return denied
            item = db.query(model).filter(getattr(model, view.pk_field) == item_id).first()
            if not item:
                return HTMLResponse("Not found", status_code=404)

            form_data = await request.form()
            old_snapshot = view._audit_snapshot(item) if view.audit_log else None
            try:
                view._apply_form_data(item, form_data)
                view.on_model_change(item, form_data, is_new=False, db=db, request=request)
                db.commit()
            except ValidationError as e:
                db.rollback()
                error_msg = e.message
            except IntegrityError as e:
                db.rollback()
                error_msg = "A required field is missing or a value already exists."
            except Exception as e:
                db.rollback()
                error_msg = str(e) or "An unexpected error occurred."
            else:
                error_msg = None
                view.after_model_change(item, form_data, is_new=False, db=db, request=request)
                if view.audit_log:
                    new_snapshot = view._audit_snapshot(item)
                    changed_keys = [k for k, v in new_snapshot.items() if old_snapshot.get(k) != v]
                    view._audit_emit(
                        action="update",
                        item=item,
                        request=request,
                        data={
                            "old": {k: old_snapshot.get(k) for k in changed_keys},
                            "new": {k: new_snapshot[k] for k in changed_keys},
                        },
                    )
            if error_msg:
                form_fields = view._prepare_form_fields(db, item)
                return templates.TemplateResponse(view.edit_template, {
                    "request": request,
                    "view": view,
                    "form_fields": form_fields,
                    "form_sections": view.form_sections,
                    "item": item,
                    "action": f"/{view.name}/{item_id}/edit",
                    "title": f"Edit {view.display_name}",
                }, headers={
                    "HX-Trigger": json.dumps({"showToast": {
                        "message": error_msg, "type": "danger", "title": "Validation Error",
                    }}),
                })
            if request.headers.get("HX-Request"):
                return HTMLResponse("", headers={"HX-Redirect": f"/{view.name}"})
            return RedirectResponse(f"/{view.name}", status_code=303)

        @self.router.post(f"/{self.name}/{{item_id}}/delete", response_class=HTMLResponse)
        def delete_item(
            request: Request,
            item_id,
            db: Session = Depends(get_db),
        ):
            denied = view._check_access(request)
            if denied:
                return denied
            if not view.can_delete:
                return HTMLResponse("Delete not allowed", status_code=403)

            item = db.query(model).filter(getattr(model, view.pk_field) == item_id).first()
            if item:
                pre_snapshot = view._audit_snapshot(item) if view.audit_log else None
                pre_item_id = getattr(item, view.pk_field, None)
                try:
                    view.on_model_delete(item, db=db)
                    db.delete(item)
                    db.commit()
                except ValidationError as e:
                    db.rollback()
                    if request.headers.get("HX-Request"):
                        return HTMLResponse("", headers={
                            "HX-Trigger": json.dumps({"showToast": {
                                "message": e.message, "type": "danger", "title": "Error",
                            }}),
                        })
                    return RedirectResponse(f"/{view.name}", status_code=303)
                else:
                    view.after_model_delete(item, db=db)
                    view._audit_emit(
                        action="delete",
                        item=None,
                        item_id=pre_item_id,
                        request=request,
                        data=pre_snapshot or {},
                    )

            if request.headers.get("HX-Request"):
                return HTMLResponse("")
            return RedirectResponse(f"/{view.name}", status_code=303)

    def _prepare_form_fields(self, db: Session, item=None) -> list:
        """Prepare form fields with current values and FK options."""
        fields = []
        for field in self.form_fields:
            f = dict(field)
            if field.get("virtual"):
                f["value"] = None
            elif item:
                f["value"] = getattr(item, field["key"])
            else:
                f["value"] = None

            if field["is_fk"]:
                if field["key"] in self.form_ajax_refs:
                    ajax_cfg = self.form_ajax_refs[field["key"]]
                    f["ajax_url"] = f"/{self.name}/ajax/{field['key']}"
                    f["placeholder"] = ajax_cfg.get("placeholder", "Type to search...")
                    if item and f["value"]:
                        fk = self.foreign_keys.get(field["key"])
                        target_model = _model_registry.get(fk.column.table.name)
                        if target_model:
                            related = db.query(target_model).filter(
                                getattr(target_model, "id") == f["value"]
                            ).first()
                            f["value_label"] = str(related) if related else ""
                else:
                    f["choices"] = self._get_fk_options(db, field["key"])

            fields.append(f)
        return fields

    def _apply_form_data(self, item, form_data):
        """Apply form data to a model instance."""
        mapper = inspect(self.model)
        for field in self.form_fields:
            key = field["key"]
            if field.get("virtual"):
                continue
            if field.get("type") == "file":
                # File uploads need special handling: save to disk and store
                # the resulting filename on the column (not the UploadFile).
                self._apply_file_field(item, field, form_data)
                continue
            if key in form_data:
                value = form_data[key]
                col = mapper.columns[key]
                col_type = type(col.type).__name__.upper()

                if col_type == "INTEGER":
                    value = int(value) if value else None
                elif col_type == "FLOAT":
                    value = float(value) if value else None
                elif col_type == "BOOLEAN":
                    value = value in ("true", "1", "on", "True")
                elif not value and col.nullable:
                    value = None

                # Reject empty values for non-nullable fields (except booleans)
                if not value and value != 0 and value is not False and not col.nullable and col.default is None:
                    label = field.get("label") or key.replace("_", " ").title()
                    raise ValidationError(f"{label} is required")

                if hasattr(col.type, "enum_class") and col.type.enum_class and value:
                    value = col.type.enum_class(value)

                setattr(item, key, value)
            else:
                # Unchecked checkboxes are not submitted in HTML forms,
                # so missing boolean fields must be explicitly set to False.
                col = mapper.columns.get(key)
                if col is not None and type(col.type).__name__.upper() == "BOOLEAN":
                    setattr(item, key, False)

    def _apply_file_field(self, item, field, form_data):
        """Save an uploaded file for a ``type: "file"`` form field.

        The uploaded file is written to disk and the resulting (relative) name
        is stored on the model column. Behaviour is configured via
        ``form_widget_overrides`` (mirrors Flask-Admin's FileUploadField args):

            base_path          directory files are saved under (required)
            relative_path      sub-path under base_path, also prefixed on the
                               stored column value (default "")
            allowed_extensions list of permitted extensions, e.g. ["lic"]
            allow_overwrite    if False, refuse to clobber an existing file
                               (default True)
            namegen            optional callable(item, upload) -> filename;
                               defaults to the sanitized uploaded filename
        """
        key = field["key"]
        upload = form_data.get(key)
        filename = getattr(upload, "filename", "") if upload is not None else ""
        label = field.get("label") or key.replace("_", " ").title()

        # No new file submitted: keep any existing value; enforce required on create.
        if not filename:
            if field.get("required") and not getattr(item, key, None):
                raise ValidationError(f"{label} is required")
            return

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        allowed = [str(e).lower().lstrip(".") for e in (field.get("allowed_extensions") or [])]
        if allowed and ext not in allowed:
            raise ValidationError(f"{label}: only {', '.join(allowed)} files are allowed")

        namegen = field.get("namegen")
        target_name = namegen(item, upload) if callable(namegen) else _secure_filename(filename)

        relative_path = field.get("relative_path", "") or ""
        base_path = field.get("base_path", "") or ""
        stored = os.path.join(relative_path, target_name) if relative_path else target_name
        dest = os.path.join(base_path, stored)

        if not field.get("allow_overwrite", True) and os.path.exists(dest):
            raise ValidationError(f"{label}: '{target_name}' already exists")

        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        data = upload.file.read()
        with open(dest, "wb") as fh:
            fh.write(data)

        setattr(item, key, stored)

    def _audit_snapshot(self, item) -> dict:
        """Capture a dict of column values from a model instance, dropping excluded fields."""
        if item is None:
            return {}
        exclude = set(self.audit_log_exclude or [])
        try:
            mapper = inspect(type(item))
        except Exception:
            return {}
        snapshot = {}
        for col in mapper.columns:
            key = col.key
            if key in exclude:
                continue
            try:
                snapshot[key] = getattr(item, key)
            except Exception:
                continue
        return snapshot

    def _audit_emit(self, *, action: str, item, request: Request, data: dict, item_id=None) -> None:
        """Invoke Admin.audit_logger with a standard event, swallowing errors."""
        if not self.audit_log:
            return
        logger = getattr(Admin, "audit_logger", None)
        if logger is None:
            return
        if item_id is None:
            try:
                item_id = getattr(item, self.pk_field, None) if item is not None else None
            except Exception:
                item_id = None
        user = None
        try:
            user = get_current_user(request) if request is not None else None
        except Exception:
            user = None
        event = {
            "action": action,
            "model_name": self.model.__name__,
            "view_name": self.name,
            "item_id": item_id,
            "user": user,
            "data": data,
            "request": request,
        }
        try:
            logger(event)
        except Exception:
            import logging
            logging.getLogger("fasthx_admin.audit").exception(
                "audit_logger raised; continuing",
            )

    def audit(
        self,
        action: str,
        *,
        item=None,
        request: Request = None,
        data: dict | None = None,
        item_id=None,
    ) -> None:
        """Emit a custom audit event from inside an endpoint body.

        Respects ``self.audit_log`` (no-op when False) and
        ``Admin.audit_logger`` (no-op when unset). Exceptions raised by the
        logger are caught + logged, never surfaced to the caller.

        Pass ``item`` to auto-derive ``item_id`` from ``pk_field``; or pass
        ``item_id`` directly. ``data`` is the payload dict you want logged —
        callers are responsible for shaping it (e.g. ``{"old": ..., "new": ...}``).
        """
        self._audit_emit(
            action=action,
            item=item,
            item_id=item_id,
            request=request,
            data=data or {},
        )

    def on_model_change(self, item, form_data, is_new: bool, db: Session, request: Request = None):
        """Called before a model is committed on create or edit.

        Use for validation and/or mutation before ``db.commit()``.
        Raise ``ValidationError`` to abort the save.

        Args:
            item: The model instance with form data applied.
            form_data: The raw form data dict.
            is_new: True for create, False for edit.
            db: The SQLAlchemy session.
            request: The current FastAPI request.
        """
        pass

    def after_model_change(self, item, form_data, is_new: bool, db: Session, request: Request = None):
        """Called after a model is successfully committed on create or edit.

        Use this for side effects like audit logging, cache invalidation,
        or sending notifications.

        Args:
            item: The model instance that was saved.
            form_data: The raw form data dict.
            is_new: True for create, False for edit.
            db: The SQLAlchemy session.
            request: The current FastAPI request.
        """
        pass

    def on_model_delete(self, item, db: Session):
        """Called before a model is deleted and committed.

        Raise ``ValidationError`` to abort the delete.

        Args:
            item: The model instance about to be deleted.
            db: The SQLAlchemy session.
        """
        pass

    def after_model_delete(self, item, db: Session):
        """Called after a model is successfully deleted and committed.

        Args:
            item: The model instance that was deleted.
            db: The SQLAlchemy session.
        """
        pass

    async def ai_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: list[str] | None = None,
        db: Session | None = None,
    ) -> str:
        """One-shot prompt against the configured AI connection. Returns response text.

        Convenience wrapper for use inside ``@CRUDView.endpoint()`` handlers.
        Pass ``tools=[...]`` to allow the model to call tools registered via
        ``@tool_registry.tool()``. Pass ``db`` if you already have a session;
        otherwise a short-lived one is opened just to load the active connection.
        """
        from .ai_chat import ai_complete as _ai_complete
        return await _ai_complete(prompt, system=system, tools=tools, db=db)

    def register(self, app):
        """Register this view's routes with the FastAPI app."""
        app.include_router(self.router, tags=[self.display_name])

    def get_nav_info(self) -> dict:
        """Return navigation info for the sidebar."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category,
            "icon": self.icon,
            "url": f"/{self.name}",
        }


class Admin:
    """
    Factory that instantiates CRUDView subclasses, registers them with a
    FastAPI app, and sets up the built-in templates and static assets.

    Usage::

        from fasthx_admin import Admin, CRUDView

        app = FastAPI()
        admin = Admin(app)
        admin.add_view(CustomerView)
    """

    celery_app: Celery | None = None
    audit_logger: Callable[[dict], None] | None = None

    def __init__(
        self,
        app: FastAPI,
        templates: Jinja2Templates | None = None,
        *,
        title: str = "Admin",
        static_url: str = "/static/fasthx-admin",
        mount_statics: bool = True,
        public_pages: set[str] | None = None,
        ai_chat: bool = False,
        extra_templates_dirs: list[str] | None = None,
        settings_admin_groups: list[str] | None = None,
        settings_admin_users: list[str] | None = None,
        celery_app: Celery | None = None,
        audit_logger: Callable[[dict], None] | None = None,
    ):
        self.app = app
        self.title = title
        self.static_url = static_url
        self.public_pages = public_pages if public_pages is not None else {"login.html"}
        self.views: list[CRUDView] = []
        self._view_map: dict[str, CRUDView] = {}
        self._custom_links: list[dict] = []
        self.ai_chat_enabled = ai_chat
        self.settings_admin_groups = settings_admin_groups
        self.settings_admin_users = settings_admin_users
        Admin.celery_app = celery_app
        Admin.audit_logger = audit_logger

        # Set up Jinja2 templates (use built-in if not provided)
        if templates is not None:
            self.templates = templates
        else:
            builtin_dir = str(_PACKAGE_DIR / "templates")
            self.templates = Jinja2Templates(directory=builtin_dir)

        # Add extra template search directories (app-level custom templates)
        if extra_templates_dirs:
            from jinja2 import FileSystemLoader
            loader = self.templates.env.loader
            existing = loader.searchpath if hasattr(loader, 'searchpath') else [loader.searchpath]
            self.templates.env.loader = FileSystemLoader(list(extra_templates_dirs) + existing)

        # Mount built-in static files
        if mount_statics:
            static_dir = _PACKAGE_DIR / "static"
            app.mount(
                static_url,
                StaticFiles(directory=str(static_dir)),
                name="fasthx-admin-static",
            )

        # Wrap TemplateResponse to inject nav context + auth check
        self._wrap_template_response()

        # Set up AI chat if enabled
        if ai_chat:
            from .ai_chat import create_ai_chat_router, ensure_ai_tables
            ensure_ai_tables()
            router = create_ai_chat_router(self)
            app.include_router(router)
            self.add_link(
                "ai_settings", "/ai/settings", "AI Settings",
                icon="robot", category="Settings",
            )
            self.add_link(
                "ai_context_settings", "/ai/settings/context", "AI Context & Tools",
                icon="puzzle", category="Settings",
            )

    def _wrap_template_response(self):
        """Monkey-patch TemplateResponse to inject nav categories and auth."""
        _original = self.templates.TemplateResponse
        admin = self

        def _patched(name, context, **kwargs):
            request = context.get("request")
            user = get_current_user(request) if request else None

            # Redirect to login if not authenticated (skip for public pages)
            if name not in admin.public_pages and not user:
                return RedirectResponse("/login", status_code=303)

            context.setdefault("current_user", user)
            context.setdefault("nav_categories", admin.get_nav_categories(user))
            context.setdefault("active_page", "")
            context.setdefault("static_url", admin.static_url)
            context.setdefault("admin_title", admin.title)
            if admin.ai_chat_enabled:
                from .ai_chat import is_chat_widget_enabled
                context.setdefault("ai_chat_enabled", is_chat_widget_enabled())
            else:
                context.setdefault("ai_chat_enabled", False)
            return _original(name, context, **kwargs)

        self.templates.TemplateResponse = _patched

    def add_view(self, view_class: type[CRUDView]) -> CRUDView:
        """Instantiate a CRUDView subclass and register its routes."""
        instance = view_class(self.templates)
        self.views.append(instance)
        self._view_map[instance.name] = instance
        instance.register(self.app)
        return instance

    def get_view(self, name: str) -> CRUDView | None:
        """Look up a registered view by name."""
        return self._view_map.get(name)

    def add_link(
        self,
        name: str,
        url: str,
        display_name: str,
        icon: str = "link",
        category: str = "Other",
        allowed_users: list[str] | None = None,
        allowed_groups: list[str] | None = None,
    ):
        """Add a custom navigation link to the sidebar."""
        self._custom_links.append({
            "name": name,
            "url": url,
            "display_name": display_name,
            "icon": icon,
            "category": category,
            "allowed_users": allowed_users,
            "allowed_groups": allowed_groups,
        })


    @staticmethod
    def _user_allowed(user: dict | None, allowed_users: list[str] | None, allowed_groups: list[str] | None) -> bool:
        """Check if a user is allowed based on allowed_users/allowed_groups lists.

        If neither list is set, returns True (visible to all).
        If either list is set, the user must match at least one entry.
        """
        if not allowed_users and not allowed_groups:
            return True
        username = (user or {}).get("username", "")
        user_groups = (user or {}).get("groups", [])
        if allowed_users and username in allowed_users:
            return True
        if allowed_groups and any(g in allowed_groups for g in user_groups):
            return True
        return False

    def get_nav_categories(self, user: dict | None = None) -> dict:
        """Build sidebar navigation from all registered views."""
        categories = defaultdict(list)
        for view in self.views:
            if not self._user_allowed(user, view.allowed_users, view.allowed_groups):
                continue
            cat = view.category or None
            categories[cat].append(view.get_nav_info())
        for link in self._custom_links:
            if not self._user_allowed(user, link.get("allowed_users"), link.get("allowed_groups")):
                continue
            cat = link.get("category") or None
            categories[cat].append({
                "name": link["name"],
                "url": link["url"],
                "display_name": link["display_name"],
                "icon": link["icon"],
            })

        # Settings category: hidden by default unless user is explicitly allowed
        # via settings_admin_users/settings_admin_groups on the Admin instance.
        # Unlike views/links (visible by default), Settings requires an explicit
        # allow list — if neither is configured, no one sees it.
        if "Settings" in categories:
            if not self.settings_admin_users and not self.settings_admin_groups:
                del categories["Settings"]
            elif not self._user_allowed(user, self.settings_admin_users, self.settings_admin_groups):
                del categories["Settings"]

        return dict(categories)
