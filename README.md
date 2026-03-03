# fasthx-admin

A modern admin interface framework for FastAPI built with HTMX, Jinja2, and Bootstrap 5. Designed as a drop-in replacement for Flask-Admin with full control over rendering.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Database Setup](#database-setup)
- [Defining Models](#defining-models)
- [The Admin Class](#the-admin-class)
- [CRUDView Configuration](#crudview-configuration)
  - [Basic Attributes](#basic-attributes)
  - [Column Configuration](#column-configuration)
  - [Column Formatters](#column-formatters)
  - [Form Configuration](#form-configuration)
  - [Form Sections (Accordion Groups)](#form-sections-accordion-groups)
  - [Form Widget Overrides](#form-widget-overrides)
  - [Row Actions](#row-actions)
  - [HTMX Polling Columns](#htmx-polling-columns)
  - [Permissions](#permissions)
- [Custom Endpoints](#custom-endpoints)
- [Dependent Dropdowns](#dependent-dropdowns)
- [Authentication](#authentication)
- [Custom Pages (Dashboard, Wizard, etc.)](#custom-pages-dashboard-wizard-etc)
- [Templates](#templates)
- [Theming](#theming)
- [Auto-Generated Routes](#auto-generated-routes)
- [Environment Variables](#environment-variables)
- [Flask-Admin Migration Guide](#flask-admin-migration-guide)
- [Running the Demo](#running-the-demo)
- [Tech Stack](#tech-stack)

---

## Features

- **Auto-generated CRUD** -- list, detail, create, edit, delete routes from SQLAlchemy models
- **Dark/light theme** -- toggle with localStorage persistence, no flash on load
- **HTMX-powered** -- live search, sortable columns, auto-polling status cells, dependent dropdowns, progress bars
- **Accordion form sections** -- group form fields into collapsible sections
- **Custom column formatters** -- render badges, links, icons, code blocks in table cells
- **Custom row actions** -- per-row buttons with HTMX (deploy, build, reset, etc.)
- **Responsive sidebar** -- auto-grouped from model metadata, collapses on mobile
- **OIDC/Keycloak auth** -- Resource Owner Password Credentials flow with group-based access
- **Dev mode** -- set `AUTH_DISABLED=1` to bypass auth entirely
- **Foreign key dropdowns** -- auto-populated from related models
- **Pagination** -- configurable page size with prev/next navigation
- **Built-in templates** -- 7 page templates + 8 partials, all customizable

---

## Installation

```bash
pip install fasthx-admin
```

With development extras (uvicorn, pytest, httpx):

```bash
pip install fasthx-admin[dev]
```

---

## Quick Start

A minimal working app in one file:

```python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Column, Integer, String
from starlette.middleware.sessions import SessionMiddleware

from fasthx_admin import Admin, CRUDView, Base, init_db

# 1. Initialise the database
engine = init_db("sqlite:///./app.db", connect_args={"check_same_thread": False})

# 2. Define a model
class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200))

    __admin_category__ = "CRM"
    __admin_icon__ = "people"
    __admin_name__ = "Customers"

    def __repr__(self):
        return f"<Customer {self.name}>"

# 3. Create the app with lifespan
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET", "change-me"))

# 4. Create admin and register views
admin = Admin(app, title="My Admin")

class CustomerView(CRUDView):
    model = Customer
    column_list = ["id", "name", "email"]

admin.add_view(CustomerView)
```

Run it:

```bash
AUTH_DISABLED=1 uvicorn app:app --reload
# Open http://127.0.0.1:8000/customers
```

This gives you a full CRUD interface with list/detail/create/edit/delete, search, sorting, pagination, and a sidebar -- all from 30 lines of code.

---

## Architecture Overview

```
fasthx_admin/
├── __init__.py       # Public API exports
├── database.py       # init_db(), get_db(), Base
├── auth.py           # OIDC login, get_current_user, AUTH_DISABLED
├── crud.py           # CRUDView base class + Admin factory
├── templates/        # Jinja2 templates (base, list, form, detail, wizard, partials)
└── static/           # CSS (dark/light theme) + JS (theme toggle, HTMX hooks)
```

**How it works:**

1. You define SQLAlchemy models inheriting from `Base`
2. You subclass `CRUDView` for each model, setting class-level configuration
3. The `Admin` factory instantiates your views, introspects the models, and auto-registers FastAPI routes
4. Built-in Jinja2 templates render list tables, detail pages, and forms
5. HTMX handles dynamic interactions (search, polling, dropdowns) without page reloads

---

## Database Setup

`fasthx_admin` uses a configurable database via `init_db()`. Call it once at startup before creating tables.

```python
from fasthx_admin import init_db, Base

# SQLite (development)
engine = init_db(
    "sqlite:///./app.db",
    connect_args={"check_same_thread": False}
)

# PostgreSQL (production)
engine = init_db("postgresql://user:pass@localhost/mydb")

# Create tables
Base.metadata.create_all(bind=engine)
```

### Available functions

| Function | Description |
|---|---|
| `init_db(url, **kwargs)` | Create engine + session factory. Returns the engine. kwargs are passed to `create_engine()`. |
| `get_db()` | FastAPI dependency that yields a database session. Auto-closes when done. |
| `get_engine()` | Returns the current engine (raises `RuntimeError` if `init_db` not called). |
| `Base` | SQLAlchemy declarative base -- use this for all your models. |

---

## Defining Models

Models are standard SQLAlchemy models that inherit from `Base`. Add optional metadata attributes to control how they appear in the admin sidebar:

```python
from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Enum as SAEnum
from sqlalchemy.orm import relationship
from fasthx_admin import Base
import enum

class DeviceStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String(100), nullable=False)
    ip_address = Column(String(45))
    status = Column(SAEnum(DeviceStatus), default=DeviceStatus.OFFLINE)
    site_id = Column(Integer, ForeignKey("sites.id"))

    site = relationship("Site", back_populates="devices")

    # --- Admin UI metadata ---
    __admin_category__ = "Network"     # Sidebar group heading
    __admin_icon__ = "router"          # Bootstrap Icons name (https://icons.getbootstrap.com)
    __admin_name__ = "Devices"         # Display label in sidebar

    def __repr__(self):
        return f"<Device {self.hostname}>"
```

### Model metadata attributes

| Attribute | Purpose | Default |
|---|---|---|
| `__admin_category__` | Groups this model under a sidebar heading | `"Other"` |
| `__admin_icon__` | Bootstrap Icons icon name | `"table"` |
| `__admin_name__` | Display name in the sidebar and page titles | Table name, title-cased |

The `__repr__` method is used to display items in foreign key dropdowns, so make it human-readable.

---

## The Admin Class

`Admin` is the central factory that ties everything together.

```python
from fasthx_admin import Admin

admin = Admin(
    app,                                    # Your FastAPI app (required)
    title="My Admin",                       # Brand name in sidebar + page titles
    static_url="/static/fasthx-admin",      # Where package CSS/JS are served
    mount_statics=True,                     # Auto-mount built-in static files
    public_pages={"login.html"},            # Templates that skip auth check
)
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `app` | `FastAPI` | required | Your FastAPI application instance |
| `templates` | `Jinja2Templates` | `None` | Custom templates (uses built-in if `None`) |
| `title` | `str` | `"Admin"` | Brand name shown in sidebar header and page titles |
| `static_url` | `str` | `"/static/fasthx-admin"` | URL path where static assets are mounted |
| `mount_statics` | `bool` | `True` | Whether to auto-mount built-in CSS/JS |
| `public_pages` | `set[str]` | `{"login.html"}` | Template names that don't require authentication |

### Methods

| Method | Description |
|---|---|
| `admin.add_view(ViewClass)` | Instantiate a CRUDView subclass and register its routes. Returns the instance. |
| `admin.get_view("name")` | Look up a registered view by its `name` attribute. |
| `admin.get_nav_categories()` | Returns the sidebar navigation structure as a dict. |
| `admin.templates` | The Jinja2Templates instance -- use for rendering custom pages. |

### What Admin does automatically

1. **Mounts static files** -- CSS and JS at the configured `static_url`
2. **Sets up Jinja2 templates** -- uses the package's built-in templates
3. **Wraps TemplateResponse** -- every template automatically gets:
   - `current_user` -- the logged-in user (or mock user if auth disabled)
   - `nav_categories` -- sidebar navigation built from all registered views
   - `static_url` -- path to static assets
   - `admin_title` -- the configured title
   - **Auth redirect** -- non-public pages redirect to `/login` if unauthenticated

---

## CRUDView Configuration

`CRUDView` is the heart of fasthx-admin. Subclass it and set class-level attributes to configure each model's admin interface.

### Basic Attributes

```python
class DeviceView(CRUDView):
    model = Device                  # Required: SQLAlchemy model class
    name = "devices"                # URL prefix (default: model.__tablename__)
    display_name = "Network Devices"  # Sidebar + page title (default: model.__admin_name__)
    category = "Network"            # Sidebar group (default: model.__admin_category__)
    icon = "router"                 # Bootstrap Icons name (default: model.__admin_icon__)
    page_size = 25                  # Records per page (default: 20)
```

### Column Configuration

Control which columns appear in the list table:

```python
class DeviceView(CRUDView):
    model = Device

    # Option A: Explicitly list columns to show (in order)
    column_list = ["id", "hostname", "ip_address", "status", "site_id"]

    # Option B: Exclude specific columns (show everything else)
    column_exclude = ["deploy_progress"]

    # If neither is set, all model columns are shown

    # Rename column headers
    column_labels = {
        "site_id": "Site",
        "ip_address": "IP Address",
    }

    # Restrict which columns are searchable (default: all String columns)
    column_searchable = ["hostname", "ip_address"]

    # Restrict which columns are sortable (default: all columns)
    column_sortable = ["id", "hostname", "status"]
```

### Column Formatters

Column formatters are functions that transform raw values into HTML for display. They receive `(value, obj)` where `value` is the column value and `obj` is the full SQLAlchemy model instance.

```python
def format_status_badge(value, obj):
    """Render an enum value as a coloured badge."""
    colors = {
        DeviceStatus.ONLINE: "success",
        DeviceStatus.OFFLINE: "secondary",
        DeviceStatus.ERROR: "danger",
    }
    color = colors.get(value, "secondary")
    label = value.value.title() if hasattr(value, "value") else str(value)
    return f'<span class="badge bg-{color}">{label}</span>'

def format_ip_code(value, obj):
    """Render a value in monospace."""
    return f'<code>{value}</code>'

def format_site_link(value, obj):
    """Render a foreign key as a clickable link to the related item."""
    if obj.site:
        return f'<a href="/sites/{obj.site.id}">{obj.site.name}</a>'
    return str(value) if value else ""

def format_external_link(value, obj):
    """Render a URL as a clickable external link."""
    return f'<a href="https://{value}" target="_blank">{value} <i class="bi bi-box-arrow-up-right"></i></a>'

class DeviceView(CRUDView):
    model = Device
    column_formatters = {
        "status": format_status_badge,
        "ip_address": format_ip_code,
        "site_id": format_site_link,
    }
```

Formatters return raw HTML strings. The templates render them with `| safe` so Bootstrap classes, icons, and links all work.

### Form Configuration

Control which fields appear in create/edit forms:

```python
class DeviceView(CRUDView):
    model = Device

    # Explicitly list form fields (default: all columns except 'id')
    form_columns = ["hostname", "ip_address", "status", "site_id"]
```

Field types are auto-detected from the SQLAlchemy column type:

| SQLAlchemy Type | HTML Input Type |
|---|---|
| `Integer`, `Float` | `<input type="number">` |
| `String`, `VARCHAR` | `<input type="text">` |
| `Text` | `<textarea>` |
| `Boolean` | `<input type="checkbox">` (toggle switch) |
| `DateTime` | `<input type="datetime-local">` |
| `Date` | `<input type="date">` |
| `Enum` | `<select>` with enum values |
| Foreign Key | `<select>` auto-populated from related model |

### Form Sections (Accordion Groups)

Group form fields into collapsible accordion sections:

```python
class DeviceView(CRUDView):
    model = Device
    form_sections = {
        "Device Info": ["hostname", "ip_address"],
        "Status": ["status"],
        "Relationships": ["site_id"],
    }
```

The first section is expanded by default. If `form_sections` is `None`, all fields render in a flat list.

### Form Widget Overrides

Customize individual form fields with extra attributes or replace their type entirely:

```python
class OrchestratorView(CRUDView):
    model = Orchestrator
    form_widget_overrides = {
        # Turn a text field into a select with static choices
        "version": {
            "type": "select",
            "choices": [
                ("6.4", "Version 6.4"),
                ("7.2", "Version 7.2"),
                ("7.4", "Version 7.4"),
            ],
        },
        # Add HTMX attributes to trigger dependent dropdowns
        "customer_id": {
            "hx_get": "/api/orchestrators-for-customer",
            "hx_target": "#orchestrator_id",
        },
        # Add placeholder text
        "hostname": {
            "placeholder": "e.g. edge-001",
        },
    }
```

### Row Actions

Add custom action buttons to each row in the list table:

```python
class EdgeView(CRUDView):
    model = FortiEdge
    row_actions = [
        {
            "label": "Deploy",              # Button text
            "icon": "rocket",               # Bootstrap Icons name
            "hx_post": "/edges/{id}/deploy",  # HTMX POST URL ({id} is replaced per row)
            "hx_target": "closest tr",      # HTMX target element
            "hx_swap": "afterend",          # HTMX swap strategy
            "class": "btn-outline-success", # Bootstrap button class
        },
        {
            "label": "Reset",
            "icon": "arrow-counterclockwise",
            "hx_post": "/edges/{id}/reset",
            "hx_target": "closest tr",
            "hx_swap": "outerHTML",
            "class": "btn-outline-warning",
            "confirm": "Reset this edge device?",  # Confirmation dialog
        },
    ]
```

Every row also gets View and Edit buttons automatically (based on permissions), plus a Delete button with confirmation.

### Row action fields

| Field | Description |
|---|---|
| `label` | Button text |
| `icon` | Bootstrap Icons name (optional) |
| `hx_post` | HTMX POST URL. `{id}` is replaced with the row's primary key. |
| `hx_target` | HTMX target selector (default: `"closest tr"`) |
| `hx_swap` | HTMX swap strategy (default: `"afterend"`) |
| `class` | CSS class for the button (default: `"btn-outline-primary"`) |
| `confirm` | If set, shows a confirmation dialog before executing |

### HTMX Polling Columns

Auto-refresh specific table cells at an interval. The framework auto-generates GET endpoints that return the current value.

```python
class EdgeView(CRUDView):
    model = FortiEdge
    htmx_columns = {
        "status": {
            "url": "/edges/{id}/status",    # Polling URL ({id} replaced per row)
            "trigger": "every 5s",          # HTMX trigger interval
        },
    }
```

This auto-generates a `GET /edges/{item_id}/status` endpoint that returns the current status value rendered through `partials/status_cell.html`. No custom endpoint code needed.

You can combine this with column formatters -- the initial render uses your formatter, and polling updates use the status_cell partial.

### Permissions

Control which operations are available:

```python
class AuditLogView(CRUDView):
    model = AuditLog
    can_create = False    # Hide "Create" button
    can_edit = False      # Hide "Edit" button on each row
    can_delete = False    # Hide "Delete" button on each row
```

All default to `True`.

---

## Custom Endpoints

Override `setup_endpoints()` to add custom routes to a view's router. These are registered alongside the auto-generated CRUD routes.

```python
from fastapi import Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from fasthx_admin import CRUDView, get_db

class OrchestratorView(CRUDView):
    model = Orchestrator

    def setup_endpoints(self):
        view = self
        model = self.model
        templates = self.templates

        # Custom action: trigger a build
        @self.router.post(f"/{self.name}/{{item_id}}/build", response_class=HTMLResponse)
        async def build(request: Request, item_id: int, db: Session = Depends(get_db)):
            orch = db.query(model).filter(model.id == item_id).first()
            if not orch:
                return HTMLResponse("Not found", status_code=404)
            orch.build_status = BuildStatus.BUILDING
            db.commit()
            # HX-Redirect tells HTMX to do a full page navigation
            return HTMLResponse("", headers={"HX-Redirect": f"/{view.name}"})

        # Custom API: return filtered options for a dependent dropdown
        @self.router.get("/api/devices-for-site", response_class=HTMLResponse)
        async def devices_for_site(
            request: Request, site_id: int = 0, db: Session = Depends(get_db)
        ):
            options = []
            if site_id:
                devices = db.query(Device).filter(Device.site_id == site_id).all()
                options = [{"id": d.id, "label": d.hostname} for d in devices]
            return templates.TemplateResponse("partials/dropdown_options.html", {
                "request": request,
                "options": options,
                "selected": None,
            })
```

### Instance state in custom endpoints

If your view needs to track state (like deployment progress), add it in `__init__`:

```python
class EdgeView(CRUDView):
    model = FortiEdge

    def __init__(self, templates):
        self.deploy_progress = {}   # Must be set BEFORE super().__init__
        super().__init__(templates)

    def setup_endpoints(self):
        view = self

        @self.router.post(f"/{self.name}/{{item_id}}/deploy", response_class=HTMLResponse)
        async def deploy(request: Request, item_id: int, db: Session = Depends(get_db)):
            view.deploy_progress[item_id] = {"progress": 0, "status": "deploying"}
            # ... start deployment logic
```

---

## Dependent Dropdowns

A common pattern: selecting a value in one dropdown filters the options in another. This uses HTMX + `form_widget_overrides` + a custom endpoint.

**Step 1: Configure the trigger dropdown**

```python
class DeviceView(CRUDView):
    model = Device
    form_widget_overrides = {
        "site_id": {
            "hx_get": "/api/devices-for-site",   # Endpoint to call on change
            "hx_target": "#device_id",            # Target <select> to update
        },
    }
```

**Step 2: Create the endpoint in `setup_endpoints()`**

```python
    def setup_endpoints(self):
        @self.router.get("/api/devices-for-site", response_class=HTMLResponse)
        async def devices_for_site(request: Request, site_id: int = 0, db: Session = Depends(get_db)):
            options = []
            if site_id:
                items = db.query(Device).filter(Device.site_id == site_id).all()
                options = [{"id": d.id, "label": d.hostname} for d in items]
            return self.templates.TemplateResponse("partials/dropdown_options.html", {
                "request": request,
                "options": options,
                "selected": None,
            })
```

The `partials/dropdown_options.html` template renders `<option>` tags that replace the target `<select>`'s contents.

---

## Authentication

fasthx-admin includes OIDC/Keycloak authentication out of the box.

### Development mode (no auth server needed)

```bash
AUTH_DISABLED=1 uvicorn app:app --reload
```

When `AUTH_DISABLED=1`, all requests get a mock user `{"username": "dev", "groups": ["/Edge-Admins"]}`.

### Production mode (Keycloak)

1. Create a `client_secrets.json` in your project root:

```json
{
  "web": {
    "token_uri": "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/token",
    "userinfo_uri": "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/userinfo",
    "client_id": "my-admin-client",
    "client_secret": "your-client-secret"
  }
}
```

2. Add login/logout routes to your app:

```python
from fasthx_admin import get_current_user, oidc_login, AuthError

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

    try:
        user = oidc_login(username, password)
    except AuthError as e:
        return admin.templates.TemplateResponse("login.html", {
            "request": request,
            "error": str(e),
            "username": username,
        })

    request.session["user"] = user
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
```

### Auth functions

| Function | Description |
|---|---|
| `get_current_user(request)` | Returns user dict from session, or mock user if `AUTH_DISABLED` |
| `oidc_login(username, password)` | Exchanges credentials via Keycloak, returns `{"username": ..., "groups": [...]}` |
| `AuthError` | Exception raised on auth failure (invalid creds, wrong group, network error) |
| `AUTH_DISABLED` | Boolean, `True` when `AUTH_DISABLED` env var is set |

### Configuring allowed groups

By default, users must be in one of these Keycloak groups:

```python
from fasthx_admin.auth import ALLOWED_GROUPS

# Modify at startup to match your Keycloak groups
ALLOWED_GROUPS.clear()
ALLOWED_GROUPS.extend(["/my-admin-group", "/superusers"])
```

---

## Custom Pages (Dashboard, Wizard, etc.)

The auto-generated CRUD views handle model pages. For custom pages like dashboards, wizards, or tools, add standard FastAPI routes and use `admin.templates` for rendering.

### Dashboard example

```python
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    stats = {
        "total_devices": db.query(Device).count(),
        "online": db.query(Device).filter(Device.status == DeviceStatus.ONLINE).count(),
    }
    return admin.templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "active_page": "dashboard",   # Highlights "Dashboard" in the sidebar
    })
```

Set `active_page` to match a sidebar link's `name` to highlight it. The built-in dashboard template (`dashboard.html`) provides summary cards, a recent items table, status breakdown, and quick action buttons.

### Root redirect

```python
@app.get("/")
async def root():
    return RedirectResponse("/dashboard")
```

---

## Templates

fasthx-admin ships with these built-in templates:

| Template | Purpose |
|---|---|
| `base.html` | Main layout -- sidebar, topbar, theme toggle, content area |
| `login.html` | Standalone login page with Keycloak SSO branding |
| `dashboard.html` | Summary cards, recent items table, status breakdown, quick actions |
| `list.html` | CRUD list view with search, sortable columns, pagination, row actions |
| `detail.html` | Read-only detail view showing all fields |
| `form.html` | Create/edit form with optional accordion sections |
| `wizard.html` | Multi-step wizard container |

### Partials (HTMX targets and includes)

| Partial | Purpose |
|---|---|
| `partials/table_body.html` | Table rows (HTMX target for live search) |
| `partials/row_actions.html` | View/Edit/Delete + custom action buttons |
| `partials/status_cell.html` | Status badge renderer (online/offline/deploying/error/etc.) |
| `partials/_form_field.html` | Single form field renderer (text/select/checkbox/textarea) |
| `partials/dropdown_options.html` | `<option>` tags for dependent dropdown responses |
| `partials/progress_bar.html` | Animated deployment progress bar with auto-polling |
| `partials/_wizard_indicators.html` | Wizard step progress indicators |
| `partials/wizard_step.html` | Wizard step content (all 4 steps) |

### Using custom templates

Pass your own Jinja2Templates instance to override any template:

```python
from fastapi.templating import Jinja2Templates

# Your templates directory can extend/override the built-in ones
templates = Jinja2Templates(directory="my_templates")
admin = Admin(app, templates=templates, mount_statics=True)
```

### Template context variables

Every template rendered through `admin.templates.TemplateResponse()` automatically receives:

| Variable | Description |
|---|---|
| `current_user` | Dict with `username` and `groups`, or `None` |
| `nav_categories` | Sidebar navigation structure |
| `active_page` | Which sidebar item to highlight |
| `static_url` | URL prefix for static assets |
| `admin_title` | The configured admin title |

---

## Theming

The built-in CSS supports dark and light themes via Bootstrap's `data-bs-theme` attribute. Dark is the default.

### Color palette

| Variable | Dark | Light |
|---|---|---|
| `--accent` | `#10b981` (emerald green) | same |
| `--bg-base` | `#1f1f1f` | `#f3f4f6` |
| `--bg-surface` | `#303030` | `#ffffff` |
| `--text` | `#ffffff` | `#1f1f1f` |
| `--danger` | `#ef4444` | same |
| `--warning` | `#f59e0b` | same |
| `--info` | `#3b82f6` | same |

Theme is toggled via the sun/moon button in the topbar and persisted in `localStorage`.

---

## Auto-Generated Routes

For each registered CRUDView, these routes are created automatically:

| Method | URL | Description |
|---|---|---|
| `GET` | `/{name}` | List view with search, sort, pagination |
| `GET` | `/{name}/create` | Create form |
| `POST` | `/{name}/create` | Submit new record |
| `GET` | `/{name}/{id}` | Detail view |
| `GET` | `/{name}/{id}/edit` | Edit form |
| `POST` | `/{name}/{id}/edit` | Submit edit |
| `POST` | `/{name}/{id}/delete` | Delete record |

Plus for each `htmx_columns` entry:

| Method | URL | Description |
|---|---|---|
| `GET` | `/{name}/{id}/{field}` | Returns current field value (for polling) |

**Example:** A view with `name = "devices"` generates:
- `GET /devices` -- list all devices
- `GET /devices/create` -- show create form
- `POST /devices/create` -- create a device
- `GET /devices/42` -- show device #42
- `GET /devices/42/edit` -- edit form for device #42
- `POST /devices/42/edit` -- save edits
- `POST /devices/42/delete` -- delete device #42

---

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `AUTH_DISABLED` | Set to `1`, `true`, or `yes` to bypass authentication | auth enabled |
| `SESSION_SECRET` | Secret key for session cookie signing | set in your app |
| `OIDC_SECRETS` | Path to Keycloak `client_secrets.json` | `./client_secrets.json` |

---

## Flask-Admin Migration Guide

fasthx-admin is designed as a drop-in conceptual replacement for Flask-Admin. Here's how the concepts map:

| Flask-Admin | fasthx-admin | Notes |
|---|---|---|
| `ModelView` | `CRUDView` subclass | Same pattern: subclass + class attributes |
| `admin.add_view(MyView(Model, db.session))` | `admin.add_view(MyView)` | No session arg needed; uses `get_db` dependency |
| `column_formatters` | `column_formatters` | Same API: `{col: fn(value, obj) -> html}` |
| `column_list` | `column_list` | Identical |
| `column_labels` | `column_labels` | Identical |
| `column_searchable_list` | `column_searchable` | Renamed |
| `column_sortable_list` | `column_sortable` | Renamed |
| `column_exclude_list` | `column_exclude` | Renamed |
| `form_columns` | `form_columns` | Identical |
| `form_create_rules` + `FieldSet()` | `form_sections` | Dict instead of list of rules |
| `form_args` | `form_widget_overrides` | Renamed, supports HTMX attrs |
| `column_extra_row_actions` | `row_actions` | List of dicts with HTMX attrs |
| `@expose()` custom endpoints | `setup_endpoints()` override | Define on `self.router` |
| `Markup()` in formatters | Raw HTML strings | Templates use `\| safe` filter |

---

## Running the Demo

The package includes a full demo application in `examples/demo/`:

```bash
git clone https://github.com/talbiston/fasthx-admin.git
cd fasthx-admin
pip install -e .[dev]
cd examples/demo
AUTH_DISABLED=1 uvicorn app:app --reload
```

Open http://127.0.0.1:8000

The demo includes:
- **3 CRUD views** -- Customers, Orchestrators, FortiEdges
- **Dashboard** -- summary cards, recent items, status breakdown
- **Deploy Wizard** -- 4-step wizard with dependent dropdowns and live progress
- **Custom formatters** -- status badges, links, monospace serial numbers
- **Row actions** -- Build, Deploy, Reset with HTMX
- **HTMX polling** -- live status updates on build_status and edge status columns
- **25 seed records** -- auto-generated on first startup

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | [FastAPI](https://fastapi.tiangolo.com/) |
| ORM | [SQLAlchemy](https://www.sqlalchemy.org/) |
| Templates | [Jinja2](https://jinja.palletsprojects.com/) |
| Frontend | [HTMX 2.0](https://htmx.org/) (CDN) |
| CSS | [Bootstrap 5.3](https://getbootstrap.com/) (CDN) |
| Icons | [Bootstrap Icons](https://icons.getbootstrap.com/) (CDN) |
| Auth | OIDC / Keycloak (via [requests](https://requests.readthedocs.io/)) |
| Server | [Uvicorn](https://www.uvicorn.org/) (dev dependency) |
| JavaScript | Minimal -- theme toggle + HTMX event hooks only |
