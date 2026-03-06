"""
Demo application showing how to use fasthx-admin.

Run with:
    AUTH_DISABLED=1 uvicorn app:app --reload
"""

from __future__ import annotations

import logging
import os
import random
import time

logging.basicConfig(level=logging.DEBUG)
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from fasthx_admin import Admin, CRUDView, Base, init_db, get_db, get_current_user, oidc_login, AuthError, tool_registry

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

admin = Admin(app, title="Admin Demo", ai_chat=True)


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


class OrchestratorView(CRUDView):
    model = Orchestrator
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
            return HTMLResponse("", headers={"HX-Redirect": f"/{view.name}"})

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
            "label": "Reset",
            "icon": "arrow-counterclockwise",
            "hx_post": "/edges/{id}/reset",
            "hx_target": "closest tr",
            "hx_swap": "outerHTML",
            "class": "btn-outline-warning",
            "confirm": "Reset this edge device?",
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

    @CRUDView.endpoint("/{name}/{item_id}/reset", methods=["POST"], response_class=HTMLResponse)
    async def reset_edge(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        edge = db.query(self.model).filter(self.model.id == item_id).first()
        if not edge:
            return HTMLResponse("Not found", status_code=404)
        edge.status = EdgeStatus.PENDING
        edge.deploy_progress = 0
        db.commit()
        self.deploy_progress.pop(item_id, None)
        return HTMLResponse("", headers={"HX-Redirect": f"/{self.name}"})


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
        user = oidc_login(username, password)
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

    stats = {
        "total_edges": total_edges,
        "online": online,
        "deploying": deploying,
        "error": error,
        "status_breakdown": status_breakdown,
        "total_customers": db.query(Customer).count(),
        "total_orchestrators": db.query(Orchestrator).count(),
    }

    return admin.templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "recent_edges": recent_edges,
        "active_page": "dashboard",
    })


# --- Wizard ---


@app.get("/wizard", response_class=HTMLResponse)
async def wizard(request: Request, db: Session = Depends(get_db)):
    customers = db.query(Customer).all()
    edges = db.query(FortiEdge).all()

    return admin.templates.TemplateResponse("wizard.html", {
        "request": request,
        "current_step": 1,
        "step": 1,
        "customers": customers,
        "edges": edges,
        "data": {},
        "active_page": "wizard",
    })


@app.post("/wizard/step/{step}", response_class=HTMLResponse)
async def wizard_step(request: Request, step: int, db: Session = Depends(get_db)):
    form_data = await request.form()
    data = dict(form_data)

    # Resolve names for the review step
    if step == 3:
        if data.get("customer_id"):
            cust = db.query(Customer).filter(Customer.id == int(data["customer_id"])).first()
            if cust:
                data["customer_name"] = cust.name
        if data.get("orchestrator_id"):
            orch = db.query(Orchestrator).filter(Orchestrator.id == int(data["orchestrator_id"])).first()
            if orch:
                data["orchestrator_name"] = f"{orch.address} ({orch.type})"
        if data.get("edge_id"):
            edge = db.query(FortiEdge).filter(FortiEdge.id == int(data["edge_id"])).first()
            if edge:
                data["edge_name"] = f"{edge.hostname} ({edge.serial_number})"

    # Start deployment on step 4
    if step == 4 and data.get("edge_id"):
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

    customers = db.query(Customer).all()
    edges = db.query(FortiEdge).all()

    return admin.templates.TemplateResponse("partials/wizard_step.html", {
        "request": request,
        "step": step,
        "data": data,
        "customers": customers,
        "edges": edges,
    })


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
