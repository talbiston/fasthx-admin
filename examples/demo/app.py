"""
Demo application showing how to use fasthx-admin.

Run with:
    AUTH_DISABLED=1 uvicorn app:app --reload
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import os
import random
import time

logging.basicConfig(level=logging.DEBUG)
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from fasthx_admin import Admin, CRUDView, WizardView, Base, init_db, get_db, get_current_user, OidcAuthenticator, AuthError, tool_registry, toast_response, console_response, console_sse_message, ValidationError

from models import Customer, Orchestrator, FortiEdge, BuildStatus, EdgeStatus

# --- Database setup ---

engine = init_db("sqlite:///./demo.db", connect_args={"check_same_thread": False})


# --- App lifecycle ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_data()
    yield


app = FastAPI(title="Admin Demo", lifespan=lifespan)

SECRET_KEY = os.environ.get("SESSION_SECRET", "dev-secret-change-in-production")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


# --- Admin setup ---

admin = Admin(app, title="Admin Demo", ai_chat=True, extra_templates_dirs=["templates"])

# OIDC authenticator — the app owns the list of authorized groups.
authenticator = OidcAuthenticator(
    allowed_groups=[
        "/sdn.automation",
        "/Edge-Admins",
        "/Edge-Support",
        "/UCPE Admins",
        "/edge_admins",
    ],
)


# --- AI Chat Tools ---


@tool_registry.tool(description="Get the total number of customers")
def customer_count(db=None):
    """Returns the total number of customers."""
    count = db.query(Customer).count()
    return f"There are {count} customers."


@tool_registry.tool(description="Look up a customer by name")
def find_customer(name: str, db=None):
    """Find a customer by name (partial match)."""
    results = db.query(Customer).filter(Customer.name.ilike(f"%{name}%")).all()
    if not results:
        return f"No customers found matching '{name}'."
    return "\n".join(f"- {c.name} (SID: {c.sid}, ADOM: {c.adom})" for c in results)


@tool_registry.tool(description="Get edge device statistics")
def edge_stats(db=None):
    """Returns edge device status breakdown."""
    total = db.query(FortiEdge).count()
    lines = [f"Total edges: {total}"]
    for status in EdgeStatus:
        count = db.query(FortiEdge).filter(FortiEdge.status == status).count()
        if count > 0:
            lines.append(f"  {status.value}: {count}")
    return "\n".join(lines)


# --- AI Chat Lifecycle Hooks ---


@tool_registry.on("session_start", description="Log when a new chat session begins")
def log_session_start(session_id, user):
    import logging
    logging.getLogger("demo.ai_hooks").info(
        "chat session started: sid=%s user=%s", session_id, user
    )


@tool_registry.on("user_prompt_submit", description="Append today's date to the user's message")
def inject_today(message):
    from datetime import date
    return f"{message}\n\n(Note: today is {date.today().isoformat()})"


@tool_registry.on("pre_tool_use", description="Block any tool whose name starts with 'delete_'")
def block_deletes(tool_name, user):
    if tool_name.startswith("delete_"):
        return f"Blocked: the '{tool_name}' tool is disabled in this demo."


@tool_registry.on("post_tool_use", description="Truncate tool results longer than 2000 chars")
def truncate_long_results(result):
    if isinstance(result, str) and len(result) > 2000:
        return result[:2000] + "\n\n[... truncated by post_tool_use hook ...]"


# --- Seed data ---


def seed_data():
    """Populate the database with mock data if empty."""
    db = next(get_db())
    if db.query(Customer).count() > 0:
        db.close()
        return

    customers = [
        Customer(name="Acme Corp", sid="ACME-001", adom="acme_adom"),
        Customer(name="GlobalTech", sid="GT-002", adom="globaltech_adom"),
        Customer(name="NetSecure Inc", sid="NS-003", adom="netsecure_adom"),
        Customer(name="CloudFirst", sid="CF-004", adom="cloudfirst_adom"),
    ]
    db.add_all(customers)
    db.flush()

    orchestrators = [
        Orchestrator(
            address="fmg1.acme.com", type="FortiManager", apiname="fmg_api_acme",
            version="7.4.3", build_status=BuildStatus.SUCCESS, customer_id=customers[0].id,
        ),
        Orchestrator(
            address="fmg2.globaltech.com", type="FortiManager", apiname="fmg_api_gt",
            version="7.4.2", build_status=BuildStatus.IDLE, customer_id=customers[1].id,
        ),
        Orchestrator(
            address="vco1.netsecure.com", type="Velocloud", apiname="vco_api_ns",
            version="5.2.1", build_status=BuildStatus.BUILDING, customer_id=customers[2].id,
        ),
        Orchestrator(
            address="fmg3.cloudfirst.io", type="FortiManager", apiname="fmg_api_cf",
            version="7.4.1", build_status=BuildStatus.FAILED, customer_id=customers[3].id,
        ),
    ]
    db.add_all(orchestrators)
    db.flush()

    statuses = [EdgeStatus.ONLINE, EdgeStatus.OFFLINE, EdgeStatus.DEPLOYING,
                EdgeStatus.ERROR, EdgeStatus.PENDING]
    edges = []
    for i in range(25):
        cust = customers[i % len(customers)]
        orch = orchestrators[i % len(orchestrators)]
        edges.append(FortiEdge(
            hostname=f"edge-{i + 1:03d}",
            serial_number=f"FGT{random.randint(100000, 999999)}",
            status=statuses[i % len(statuses)],
            deploy_progress=100 if statuses[i % len(statuses)] == EdgeStatus.ONLINE else 0,
            customer_id=cust.id,
            orchestrator_id=orch.id,
        ))
    db.add_all(edges)
    db.commit()
    db.close()


# --- CRUD View Registration ---

# Custom column formatters (equivalent to Flask-Admin's column_formatters + Markup())
def format_build_status(value, obj):
    colors = {
        BuildStatus.IDLE: "secondary",
        BuildStatus.BUILDING: "warning",
        BuildStatus.SUCCESS: "success",
        BuildStatus.FAILED: "danger",
    }
    icons = {
        BuildStatus.IDLE: "circle",
        BuildStatus.BUILDING: "arrow-repeat",
        BuildStatus.SUCCESS: "check-circle",
        BuildStatus.FAILED: "x-circle",
    }
    if value is None:
        return ""
    color = colors.get(value, "secondary")
    icon = icons.get(value, "circle")
    spin = " spin" if value == BuildStatus.BUILDING else ""
    label = value.value.title() if hasattr(value, "value") else str(value)
    return f'<span class="badge bg-{color}"><i class="bi bi-{icon}{spin}"></i> {label}</span>'


def format_edge_status(value, obj):
    colors = {
        EdgeStatus.ONLINE: "success",
        EdgeStatus.OFFLINE: "secondary",
        EdgeStatus.DEPLOYING: "warning",
        EdgeStatus.ERROR: "danger",
        EdgeStatus.PENDING: "info",
    }
    if value is None:
        return ""
    color = colors.get(value, "secondary")
    label = value.value.title() if hasattr(value, "value") else str(value)
    return f'<span class="badge bg-{color}">{label}</span>'


def format_address_link(value, obj):
    return f'<a href="https://{value}" target="_blank">{value} <i class="bi bi-box-arrow-up-right"></i></a>'


def format_serial(value, obj):
    return f'<code>{value}</code>'


def format_customer_fk(value, obj):
    if obj.customer:
        return f'<a href="/customers/{obj.customer.id}">{obj.customer.name}</a>'
    return str(value) if value else ""


def format_orchestrator_fk(value, obj):
    if obj.orchestrator:
        return f'<a href="/orchestrators/{obj.orchestrator.id}">{obj.orchestrator.address}</a>'
    return str(value) if value else ""


# --- View classes (subclass CRUDView, registered via Admin factory) ---


class CustomerView(CRUDView):
    model = Customer
    column_list = ["id", "name", "sid", "adom"]
    form_sections = {"Basic Info": ["name", "sid"], "Configuration": ["adom"]}

    def validate(self, item, form_data, is_new):
        if not item.name or len(item.name.strip()) < 2:
            raise ValidationError("Customer name must be at least 2 characters")
        if not item.sid or not item.sid.strip():
            raise ValidationError("SID is required")


class OrchestratorView(CRUDView):
    model = Orchestrator
    allowed_users = ["dev"]
    column_list = ["id", "address", "type", "apiname", "version", "build_status", "customer_id"]
    column_formatters = {
        "build_status": format_build_status,
        "address": format_address_link,
        "customer_id": format_customer_fk,
    }
    column_labels = {"customer_id": "Customer", "apiname": "API Name"}
    htmx_columns = {
        "build_status": {
            "url": "/orchestrators/{id}/build-status",
            "trigger": "every 3s",
        },
    }
    row_actions = [
        {
            "label": "Build",
            "icon": "hammer",
            "hx_post": "/orchestrators/{id}/build",
            "hx_target": "closest tr",
            "hx_swap": "outerHTML",
            "class": "btn-outline-primary",
            "loading": True,
        },
    ]
    form_sections = {
        "Connection": ["address", "type", "apiname"],
        "Details": ["version", "customer_id", "dedicated_fortimanager"],
    }
    form_widget_overrides = {
        "customer_id": {
            "hx_get": "/api/orchestrators-for-customer",
            "hx_target": "#orchestrator_id",
        },
        "version": {
            "type": "select",
            "choices": [
                ("6.4", "Adom version 6.4"),
                ("7.2", "Adom version 7.2"),
                ("7.4", "Adom version 7.4"),
            ],
        },
    }

    def setup_endpoints(self):
        view = self
        model = self.model
        templates = self.templates

        @self.router.post(f"/{self.name}/{{item_id}}/build", response_class=HTMLResponse)
        async def build_orchestrator(request: Request, item_id: int, db: Session = Depends(get_db)):
            orch = db.query(model).filter(model.id == item_id).first()
            if not orch:
                return HTMLResponse("Not found", status_code=404)
            orch.build_status = BuildStatus.BUILDING
            db.commit()
            # Refresh the list in place, preserving the active search/filter/sort
            # state instead of redirecting to a bare URL (which would reset it).
            return toast_response("Build started", type="success", refresh=True, request=request)

        @self.router.get("/api/orchestrators-for-customer", response_class=HTMLResponse)
        async def orchestrators_for_customer(
            request: Request, customer_id: int = 0, db: Session = Depends(get_db),
        ):
            options = []
            if customer_id:
                orchs = (
                    db.query(model)
                    .filter(model.customer_id == customer_id)
                    .all()
                )
                options = [{"id": o.id, "label": f"{o.address} ({o.type})"} for o in orchs]
            return templates.TemplateResponse("partials/dropdown_options.html", {
                "request": request,
                "options": options,
                "selected": None,
            })


class EdgeView(CRUDView):
    model = FortiEdge
    name = "edges"
    display_name = "FortiEdges"
    column_list = ["id", "hostname", "serial_number", "status", "customer_id", "orchestrator_id"]
    column_formatters = {
        "status": format_edge_status,
        "serial_number": format_serial,
        "customer_id": format_customer_fk,
        "orchestrator_id": format_orchestrator_fk,
    }
    column_labels = {"customer_id": "Customer", "orchestrator_id": "Orchestrator"}
    htmx_columns = {
        "status": {
            "url": "/edges/{id}/status",
            "trigger": "every 5s",
        },
    }
    row_actions = [
        {
            "label": "Deploy",
            "icon": "rocket",
            "hx_post": "/edges/{id}/deploy",
            "hx_swap": "afterend",
            "hx_target": "closest tr",
            "class": "btn-outline-success",
        },
        {
            "label": "Logs",
            "icon": "terminal",
            "hx_get": "/edges/{id}/logs",
            "class": "btn-outline-info",
        },
        {
            "label": "Diagnostics",
            "icon": "activity",
            "hx_post": "/edges/{id}/diagnostics",
            "class": "btn-outline-secondary",
        },
        {
            "label": "Reset",
            "icon": "arrow-counterclockwise",
            "hx_post": "/edges/{id}/reset",
            "hx_target": "closest tr",
            "hx_swap": "outerHTML",
            "class": "btn-outline-warning",
            "confirm": "Reset this edge device?",
        },
    ]
    multi_row_actions = [
        {
            "label": "Reset Selected",
            "icon": "arrow-counterclockwise",
            "hx_post": "/edges/bulk-reset",
            "confirm": "Reset all selected edges?",
        },
        {
            "label": "Delete Selected",
            "icon": "trash",
            "hx_post": "/edges/bulk-delete",
            "confirm": "Delete all selected edges?",
            "class": "text-danger",
        },
    ]
    form_sections = {
        "Device Info": ["hostname", "serial_number"],
        "Status": ["status"],
        "Relationships": ["customer_id", "orchestrator_id"],
    }
    form_ajax_refs = {
        "customer_id": {
            "model": Customer,
            "fields": ["name", "sid"],
            "placeholder": "Search customers...",
            "page_size": 10,
        },
    }

    def __init__(self, templates):
        self.deploy_progress: Dict[int, dict] = {}
        super().__init__(templates)

    # --- Custom endpoints (decorator style) ---

    @CRUDView.endpoint("/{name}/{item_id}/deploy", methods=["POST"], response_class=HTMLResponse)
    async def deploy_edge(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        edge = db.query(self.model).filter(self.model.id == item_id).first()
        if not edge:
            return HTMLResponse("Not found", status_code=404)
        edge.status = EdgeStatus.DEPLOYING
        edge.deploy_progress = 0
        db.commit()
        self.deploy_progress[item_id] = {
            "progress": 0,
            "status": "deploying",
            "started": time.time(),
        }
        colspan = self.get_colspan()
        return self.templates.TemplateResponse("partials/progress_bar.html", {
            "request": request,
            "edge_id": item_id,
            "progress": 0,
            "status": "Starting...",
            "colspan": colspan,
        })

    @CRUDView.endpoint("/{name}/{item_id}/progress", methods=["GET"], response_class=HTMLResponse)
    async def edge_progress(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        state = self.deploy_progress.get(item_id, {"progress": 0, "status": "unknown"})
        if state["progress"] < 100:
            state["progress"] = min(100, state["progress"] + random.randint(5, 15))
            self.deploy_progress[item_id] = state
        if state["progress"] >= 100:
            edge = db.query(self.model).filter(self.model.id == item_id).first()
            if edge:
                edge.status = EdgeStatus.ONLINE
                edge.deploy_progress = 100
                db.commit()
            state["status"] = "Complete"
        colspan = self.get_colspan()
        return self.templates.TemplateResponse("partials/progress_bar.html", {
            "request": request,
            "edge_id": item_id,
            "progress": state["progress"],
            "status": state.get("status", "deploying"),
            "colspan": colspan,
        })

    # --- Terminal console demos ---

    @CRUDView.endpoint("/{name}/{item_id}/logs", methods=["GET"])
    async def view_logs(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        """Static log output in a terminal console."""
        edge = db.query(self.model).filter(self.model.id == item_id).first()
        if not edge:
            return HTMLResponse("Not found", status_code=404)
        log_lines = [
            f"\033[90m[2026-04-14 08:00:01]\033[0m \033[32m[INFO]\033[0m  Device \033[1m{edge.hostname}\033[0m booted successfully",
            f"\033[90m[2026-04-14 08:00:02]\033[0m \033[32m[INFO]\033[0m  Firmware version: 7.4.1 build1234",
            f"\033[90m[2026-04-14 08:00:03]\033[0m \033[32m[INFO]\033[0m  Serial: {edge.serial_number}",
            f"\033[90m[2026-04-14 08:00:04]\033[0m \033[32m[INFO]\033[0m  Connecting to orchestrator...",
            f"\033[90m[2026-04-14 08:00:05]\033[0m \033[32m[OK]\033[0m   Tunnel established",
            f"\033[90m[2026-04-14 08:00:06]\033[0m \033[32m[INFO]\033[0m  Applying security policies...",
            f"\033[90m[2026-04-14 08:00:07]\033[0m \033[33m[WARN]\033[0m  Certificate expires in 30 days",
            f"\033[90m[2026-04-14 08:00:08]\033[0m \033[32m[OK]\033[0m   All services running",
            f"\033[90m[2026-04-14 08:00:09]\033[0m \033[32m[INFO]\033[0m  Status: \033[1;32m{edge.status.value}\033[0m",
        ]
        return console_response(
            title=f"Logs — {edge.hostname}",
            output="\n".join(log_lines) + "\n",
        )

    @CRUDView.endpoint("/{name}/{item_id}/diagnostics", methods=["POST"])
    async def run_diagnostics(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        """Start a diagnostics check with streaming SSE output."""
        edge = db.query(self.model).filter(self.model.id == item_id).first()
        if not edge:
            return HTMLResponse("Not found", status_code=404)
        return console_response(
            title=f"Diagnostics — {edge.hostname}",
            output="",
            stream_url=f"/{self.name}/{item_id}/diagnostics-stream",
        )

    @CRUDView.endpoint("/{name}/{item_id}/diagnostics-stream", methods=["GET"])
    async def diagnostics_stream(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        """SSE endpoint that streams diagnostic output."""
        edge = db.query(self.model).filter(self.model.id == item_id).first()
        hostname = edge.hostname if edge else f"edge-{item_id}"

        async def generate():
            yield console_sse_message(
                f"Starting diagnostics for \033[1m{hostname}\033[0m...\n\n",
                css_class="ansi-green",
            )
            checks = [
                ("Checking network connectivity", True),
                ("Verifying DNS resolution", True),
                ("Testing tunnel latency", True),
                ("Validating certificate chain", random.choice([True, True, False])),
                ("Checking firmware version", True),
                ("Scanning open ports", True),
                ("Verifying policy sync", True),
                ("Testing HA failover", random.choice([True, False])),
            ]
            for step, success in checks:
                await asyncio.sleep(random.uniform(0.3, 1.0))
                if success:
                    yield console_sse_message(f"  \033[32m✓\033[0m {step}\n")
                else:
                    yield console_sse_message(f"  \033[33m⚠\033[0m {step} — \033[33mwarning\033[0m\n")

            await asyncio.sleep(0.5)
            yield console_sse_message(
                f"\n\033[1mDiagnostics complete.\033[0m\n",
                css_class="ansi-green",
            )

        return StreamingResponse(generate(), media_type="text/event-stream")

    @CRUDView.endpoint("/{name}/bulk-reset", methods=["POST"], response_class=HTMLResponse)
    async def bulk_reset(self, request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        ids = [int(i) for i in form.get("ids", "").split(",") if i.strip()]
        count = 0
        for eid in ids:
            edge = db.query(self.model).filter(self.model.id == eid).first()
            if edge:
                edge.status = EdgeStatus.PENDING
                edge.deploy_progress = 0
                self.deploy_progress.pop(eid, None)
                count += 1
        db.commit()
        # Bulk actions submit via fetch() + window.location.reload(), so the list
        # reloads its current (filtered) URL on its own — the filter is preserved
        # by the reload. toast_response carries the message in a cookie that
        # survives that reload (a refresh=True HX-Location header would be ignored).
        return toast_response(f"Reset {count} edges", type="success", redirect=f"/{self.name}")

    @CRUDView.endpoint("/{name}/bulk-delete", methods=["POST"], response_class=HTMLResponse)
    async def bulk_delete(self, request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        ids = [int(i) for i in form.get("ids", "").split(",") if i.strip()]
        count = db.query(self.model).filter(self.model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        # Bulk actions submit via fetch() + window.location.reload() (see bulk_reset),
        # so the filter is preserved by the reload and the toast rides a cookie.
        return toast_response(f"Deleted {count} edges", type="success", redirect=f"/{self.name}")

    @CRUDView.endpoint("/{name}/{item_id}/reset", methods=["POST"], response_class=HTMLResponse)
    async def reset_edge(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        edge = db.query(self.model).filter(self.model.id == item_id).first()
        if not edge:
            return toast_response("Edge not found", type="danger", status_code=404)
        edge.status = EdgeStatus.PENDING
        edge.deploy_progress = 0
        db.commit()
        self.deploy_progress.pop(item_id, None)
        # Refresh the list in place, preserving the active search/filter/sort state.
        return toast_response("Edge reset successfully", type="success", refresh=True, request=request)


# --- Register views ---

admin.add_view(CustomerView)
admin.add_view(OrchestratorView)
admin.add_view(EdgeView)


# --- Auth routes ---


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return admin.templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "username": None,
    })


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")

    if not username or not password:
        return admin.templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Please enter both username and password",
            "username": username,
        })

    try:
        user = authenticator.oidc_login(username, password)
    except AuthError as e:
        return admin.templates.TemplateResponse("login.html", {
            "request": request,
            "error": str(e),
            "username": username,
        })
    except Exception:
        import traceback
        logging.getLogger("auth").error("Login failed:\n%s", traceback.format_exc())
        return admin.templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Unable to connect to authentication server",
            "username": username,
        })

    request.session["user"] = user
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- Dashboard ---


@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    total_edges = db.query(FortiEdge).count()
    online = db.query(FortiEdge).filter(FortiEdge.status == EdgeStatus.ONLINE).count()
    deploying = db.query(FortiEdge).filter(FortiEdge.status == EdgeStatus.DEPLOYING).count()
    error = db.query(FortiEdge).filter(FortiEdge.status == EdgeStatus.ERROR).count()

    status_breakdown = {}
    for s in EdgeStatus:
        count = db.query(FortiEdge).filter(FortiEdge.status == s).count()
        if count > 0:
            status_breakdown[s.value] = count

    recent_edges = (
        db.query(FortiEdge)
        .order_by(FortiEdge.id.desc())
        .limit(10)
        .all()
    )

    dashboard_cards = [
        {
            "label": "Total Edges",
            "value": total_edges,
            "icon": "shield",
            "link": "/edges",
        },
        {
            "label": "Online",
            "value": online,
            "color": "text-success",
            "icon": "check-circle",
            "icon_color": "text-success",
            "bg": "bg-success-subtle",
            "link": "/edges?q=online",
        },
        {
            "label": "Deploying",
            "value": deploying,
            "color": "text-warning",
            "icon": "arrow-repeat",
            "icon_color": "text-warning",
            "bg": "bg-warning-subtle",
            "link": "/edges?q=deploying",
        },
        {
            "label": "Errors",
            "value": error,
            "color": "text-danger",
            "icon": "exclamation-triangle",
            "icon_color": "text-danger",
            "bg": "bg-danger-subtle",
            "link": "/edges?q=error",
        },
    ]

    dashboard_table = {
        "title": "Recent Edge Devices",
        "link": "/edges",
        "columns": [
            {"key": "hostname", "label": "Hostname", "link": "/edges/{id}"},
            {"key": "serial_number", "label": "Serial", "code": True},
            {"key": "customer_name", "label": "Customer"},
            {"key": "status_value", "label": "Status", "status": True},
        ],
        "rows": [
            {
                "id": e.id,
                "hostname": e.hostname,
                "serial_number": e.serial_number,
                "customer_name": e.customer.name if e.customer else "N/A",
                "status_value": e.status.value,
            }
            for e in recent_edges
        ],
    }

    dashboard_stats = {
        "title": "Status Breakdown",
        "status_breakdown": status_breakdown,
        "counters_title": "Counts",
        "counters": [
            {"label": "Customers", "value": db.query(Customer).count()},
            {"label": "Orchestrators", "value": db.query(Orchestrator).count()},
        ],
    }

    dashboard_actions = [
        {"label": "Deploy Wizard", "url": "/wizard", "icon": "magic", "class": "btn-primary"},
        {"label": "Add Edge", "url": "/edges/create", "icon": "plus-lg"},
        {"label": "Add Customer", "url": "/customers/create", "icon": "plus-lg"},
    ]

    return admin.templates.TemplateResponse("dashboard.html", {
        "request": request,
        "dashboard_cards": dashboard_cards,
        "dashboard_table": dashboard_table,
        "dashboard_stats": dashboard_stats,
        "dashboard_actions": dashboard_actions,
        "active_page": "dashboard",
    })


# --- Wizard ---
#
# A WizardView with no ``model``: it doesn't create a row, it drives a
# deployment. Every field is virtual, so each one declares its own widget in
# form_widget_overrides. Compare with a model-backed wizard, where the fields
# are just column names and the final step saves for you.


class DeployWizard(WizardView):
    name = "wizard"
    display_name = "Deploy Wizard"
    category = "Wizards"
    icon = "magic"
    description = "Push a WAN configuration to an edge device."

    steps = [
        {
            "label": "Select Device",
            "title": "Step 1: Select Customer & Edge",
            "description": "Choose the customer and edge device to deploy.",
            "fields": ["customer_id", "orchestrator_id", "edge_id"],
        },
        {
            "label": "Configure WAN",
            "title": "Step 2: Configure WAN",
            "description": "Configure the WAN interface settings.",
            "fields": ["wan_ip", "wan_subnet", "wan_gateway", "wan_dns"],
        },
        {
            "label": "Review",
            "title": "Step 3: Review",
            "description": "Review your deployment configuration before proceeding.",
            "review": True,
        },
        {
            # Terminal step: no Back/Next, the template polls until it's done.
            "label": "Deploy",
            "title": "Step 4: Deployment",
            "template": "wizard_deploy_progress.html",
            "nav": False,
        },
    ]

    form_widget_overrides = {
        "customer_id": {
            "label": "Customer",
            "type": "select",
            "required": True,
            # Callable choices are resolved per request with a live db session.
            "choices": lambda db: [
                (c.id, f"{c.name} ({c.sid})") for c in db.query(Customer).all()
            ],
            # Narrow the orchestrator list when the customer changes.
            "hx_get": "/api/orchestrators-for-customer",
            "hx_target": "#orchestrator_id",
        },
        "orchestrator_id": {
            "label": "Orchestrator",
            "type": "select",
            "required": True,
            "choices": lambda db: [
                (o.id, f"{o.address} ({o.type})") for o in db.query(Orchestrator).all()
            ],
        },
        "edge_id": {
            "label": "Edge Device",
            "type": "select",
            "required": True,
            "choices": lambda db: [
                (e.id, f"{e.hostname} ({e.serial_number})") for e in db.query(FortiEdge).all()
            ],
        },
        "wan_ip": {"label": "WAN IP Address", "required": True, "placeholder": "192.168.1.1"},
        "wan_subnet": {"label": "Subnet Mask", "required": True, "placeholder": "255.255.255.0"},
        "wan_gateway": {"label": "Gateway", "placeholder": "192.168.1.254"},
        "wan_dns": {"label": "DNS Server", "placeholder": "8.8.8.8"},
    }

    def on_step(self, step, data, db, request=None):
        """Validate the WAN step, and kick off the deploy when entering step 4."""
        if step == 2:
            ip = data.get("wan_ip", "")
            if ip.count(".") != 3:
                raise ValidationError(f"'{ip}' is not a valid IPv4 address")

        if step == 3:
            edge_id = int(data["edge_id"])
            edge = db.query(FortiEdge).filter(FortiEdge.id == edge_id).first()
            if edge:
                edge.status = EdgeStatus.DEPLOYING
                edge.deploy_progress = 0
                db.commit()
            admin.get_view("edges").deploy_progress[edge_id] = {
                "progress": 0,
                "status": "deploying",
                "started": time.time(),
            }


admin.add_view(DeployWizard)


@app.get("/wizard/deploy-status/{edge_id}", response_class=HTMLResponse)
async def wizard_deploy_status(request: Request, edge_id: int, db: Session = Depends(get_db)):
    edge_deploy = admin.get_view("edges").deploy_progress
    state = edge_deploy.get(edge_id, {"progress": 0, "status": "unknown"})

    if state["progress"] < 100:
        state["progress"] = min(100, state["progress"] + random.randint(3, 10))
        edge_deploy[edge_id] = state

    done = state["progress"] >= 100

    if done:
        edge = db.query(FortiEdge).filter(FortiEdge.id == edge_id).first()
        if edge:
            edge.status = EdgeStatus.ONLINE
            edge.deploy_progress = 100
            db.commit()

    if done:
        return HTMLResponse(f"""
            <div class="text-center py-3">
                <i class="bi bi-check-circle-fill text-success" style="font-size: 3rem;"></i>
                <h5 class="mt-2 text-success">Deployment Complete!</h5>
                <p class="text-muted">Edge device has been deployed successfully.</p>
                <a href="/edges/{edge_id}" class="btn btn-primary">View Edge</a>
                <a href="/wizard" class="btn btn-outline-secondary">Deploy Another</a>
            </div>
        """)

    status_text = "Uploading configuration..." if state["progress"] < 40 else \
                  "Applying policies..." if state["progress"] < 70 else \
                  "Verifying connectivity..."

    return HTMLResponse(f"""
        <div class="spinner-border text-primary mb-3" role="status"></div>
        <p class="text-muted">{status_text}</p>
        <div class="progress mx-auto" style="max-width: 400px; height: 20px;">
            <div class="progress-bar progress-bar-striped progress-bar-animated"
                 style="width: {state['progress']}%">{state['progress']}%</div>
        </div>
    """)
