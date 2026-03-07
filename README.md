# fasthx-admin

A modern admin interface framework for FastAPI built with HTMX, Jinja2, and Bootstrap 5. Designed as a drop-in replacement for Flask-Admin with full control over rendering.

## Screenshots

| List View | Form with Sections |
|---|---|
| ![List View](https://raw.githubusercontent.com/talbiston/fasthx-admin/main/docs/screenshot-list.png) | ![Form View](https://raw.githubusercontent.com/talbiston/fasthx-admin/main/docs/screenshot-form.png) |

| Detail View | Toast Notifications |
|---|---|
| ![Detail View](https://raw.githubusercontent.com/talbiston/fasthx-admin/main/docs/screenshot-detail.png) | ![Toast Notification](https://raw.githubusercontent.com/talbiston/fasthx-admin/main/docs/screenshot-toast.png) |

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
  - [AJAX Select (Searchable Foreign Keys)](#ajax-select-searchable-foreign-keys)
  - [Row Actions](#row-actions)
  - [HTMX Polling Columns](#htmx-polling-columns)
  - [Permissions](#permissions)
- [Custom Endpoints](#custom-endpoints)
  - [Endpoint Decorator (Recommended)](#endpoint-decorator-recommended)
  - [setup_endpoints Override (Legacy)](#setup_endpoints-override-legacy)
  - [Instance State in Custom Endpoints](#instance-state-in-custom-endpoints)
- [Dependent Dropdowns](#dependent-dropdowns)
- [Toast Notifications](#toast-notifications)
- [Validation](#validation)
- [Progress Bar](#progress-bar)
- [Authentication](#authentication)
- [AI Chat (Optional)](#ai-chat-optional)
  - [Enabling AI Chat](#enabling-ai-chat)
  - [Installing the AI Dependency](#installing-the-ai-dependency)
  - [Registering Tools](#registering-tools)
  - [Configuring via the Settings UI](#configuring-via-the-settings-ui)
  - [How It Works](#how-it-works)
  - [Custom Providers](#custom-providers)
  - [AI Chat API Endpoints](#ai-chat-api-endpoints)
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
- **AJAX select fields** -- searchable, paginated foreign key selects via HTMX (replaces Flask-Admin's `form_ajax_refs`)
- **Pagination** -- configurable page size with prev/next navigation
- **Built-in templates** -- 7 page templates + 8 partials, all customizable
- **AI chat widget (optional)** -- pluggable LLM-powered assistant with tool calling, settings UI, and OpenAI-compatible provider

---

## Installation

```bash
pip install fasthx-admin
```

With AI chat support (adds `httpx`):

```bash
pip install fasthx-admin[ai]
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
| `ai_chat` | `bool` | `False` | Enable the AI chat widget and settings pages (requires `fasthx-admin[ai]`) |

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

### AJAX Select (Searchable Foreign Keys)

For foreign key fields with large option sets, use `form_ajax_refs` to replace the standard dropdown with a searchable, paginated select powered by HTMX. This is the fasthx-admin equivalent of Flask-Admin's `form_ajax_refs`.

```python
from myapp.models import Offering, Server

class OfferingView(CRUDView):
    model = Offering

    form_ajax_refs = {
        "serverid": {
            "model": Server,           # The related SQLAlchemy model
            "fields": ["hostname"],     # Columns to search against (ilike)
            "placeholder": "Please select uCPE",  # Search input placeholder
            "page_size": 10,            # Results per page (default: 10)
        }
    }
```

**How it works:**

1. The form renders a text search input above a multi-row `<select>` (instead of a single dropdown with all options)
2. As the user types, HTMX fires a `GET /{view}/ajax/{field}?q=<term>` request after a 300ms debounce
3. The endpoint filters the target model using `ilike` on the configured `fields` and returns paginated `<option>` HTML fragments
4. If more results exist beyond `page_size`, an "infinite scroll" trigger auto-loads the next page when the user scrolls to the bottom of the select list (using `hx-trigger="intersect once"`)
5. On edit forms, the currently selected value is pre-populated in the select

**Configuration options:**

| Key | Type | Default | Description |
|---|---|---|---|
| `model` | SQLAlchemy model | *(required)* | The related model to search |
| `fields` | `list[str]` | `[]` | Model columns to search with `ilike` |
| `placeholder` | `str` | `"Type to search..."` | Placeholder text for the search input |
| `page_size` | `int` | `10` | Number of results per HTMX request |

**Auto-registered endpoint:**

Each `form_ajax_refs` entry registers a `GET /{view_name}/ajax/{field_key}` route that accepts:
- `q` -- search term (optional)
- `page` -- page number (default: 1)

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

Add custom routes to a CRUDView using the `@CRUDView.endpoint` decorator. These are registered alongside the auto-generated CRUD routes.

### Endpoint Decorator (Recommended)

Decorate methods directly on the class. Use `{name}` in the path — it's automatically replaced with `self.name` at init time.

```python
from fastapi import Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from fasthx_admin import CRUDView, get_db

class OrchestratorView(CRUDView):
    model = Orchestrator

    # Custom action: trigger a build
    @CRUDView.endpoint("/{name}/{item_id}/build", methods=["POST"], response_class=HTMLResponse)
    async def build(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        orch = db.query(self.model).filter(self.model.id == item_id).first()
        if not orch:
            return HTMLResponse("Not found", status_code=404)
        orch.build_status = BuildStatus.BUILDING
        db.commit()
        # HX-Redirect tells HTMX to do a full page navigation
        return HTMLResponse("", headers={"HX-Redirect": f"/{self.name}"})

    # Custom API: return filtered options for a dependent dropdown
    # For non-{name} paths, use the literal path string
    @CRUDView.endpoint("/api/devices-for-site", methods=["GET"], response_class=HTMLResponse)
    async def devices_for_site(self, request: Request, site_id: int = 0, db: Session = Depends(get_db)):
        options = []
        if site_id:
            devices = db.query(Device).filter(Device.site_id == site_id).all()
            options = [{"id": d.id, "label": d.hostname} for d in devices]
        return self.templates.TemplateResponse("partials/dropdown_options.html", {
            "request": request,
            "options": options,
            "selected": None,
        })
```

**Key points:**
- `{name}` in the path is replaced with the view's `name` attribute
- `methods=["POST"]` or `methods=["GET"]` — defaults to `["GET"]` if omitted
- Any extra kwargs (e.g. `response_class`) are passed to FastAPI's `add_api_route`
- `self` gives direct access to `self.model`, `self.name`, `self.templates`, etc.

### setup_endpoints Override (Legacy)

The older `setup_endpoints()` override still works and can be used alongside decorators:

```python
class MyView(CRUDView):
    model = MyModel

    def setup_endpoints(self):
        @self.router.post(f"/{self.name}/{{item_id}}/action", response_class=HTMLResponse)
        async def action(request: Request, item_id: int, db: Session = Depends(get_db)):
            ...
```

### Instance State in Custom Endpoints

If your view needs to track state (like deployment progress), add it in `__init__`:

```python
class EdgeView(CRUDView):
    model = FortiEdge

    def __init__(self, templates):
        self.deploy_progress = {}   # Must be set BEFORE super().__init__
        super().__init__(templates)

    @CRUDView.endpoint("/{name}/{item_id}/deploy", methods=["POST"], response_class=HTMLResponse)
    async def deploy(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        self.deploy_progress[item_id] = {"progress": 0, "status": "deploying"}
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

**Step 2: Create the endpoint**

```python
    @CRUDView.endpoint("/api/devices-for-site", methods=["GET"], response_class=HTMLResponse)
    async def devices_for_site(self, request: Request, site_id: int = 0, db: Session = Depends(get_db)):
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

## Toast Notifications

fasthx-admin includes a built-in toast notification system powered by Bootstrap toasts and HTMX triggers. Toasts appear in the bottom-right corner and auto-dismiss after 5 seconds.

### toast_response helper

Use `toast_response()` in custom endpoints to show a toast after an action:

```python
from fasthx_admin import CRUDView, toast_response

class EdgeView(CRUDView):
    model = FortiEdge

    @CRUDView.endpoint("/{name}/{item_id}/deploy", methods=["POST"])
    async def deploy(self, request: Request, item_id: int, db: Session = Depends(get_db)):
        edge = db.query(self.model).filter(self.model.id == item_id).first()
        if not edge:
            return toast_response("Edge not found", type="danger", status_code=404)

        # ... start deployment ...
        return toast_response("Deployment started!", type="success", redirect=f"/{self.name}")
```

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `message` | The toast message text |
| `type` | `"success"`, `"danger"`, `"warning"`, or `"info"` (default) |
| `title` | Optional title (defaults to capitalised type) |
| `redirect` | Optional URL — adds `HX-Redirect` header for page navigation after toast |
| `status_code` | HTTP status code (default 200) |

### JavaScript API

You can also trigger toasts from client-side JavaScript:

```js
showToast({ message: "Saved!", type: "success" });
showToast({ message: "Something went wrong", type: "danger", title: "Error" });
```

---

## Validation

Override the `validate()` method on a CRUDView to add custom validation to create and edit forms. When validation fails, the form re-renders with the user's values preserved and a danger toast is shown.

```python
from fasthx_admin import CRUDView, ValidationError

class CustomerView(CRUDView):
    model = Customer

    def validate(self, item, form_data, is_new):
        if not item.name or len(item.name.strip()) < 2:
            raise ValidationError("Customer name must be at least 2 characters")
        if is_new and not item.sid:
            raise ValidationError("SID is required for new customers")
```

**How it works:**

1. User submits the create or edit form
2. `_apply_form_data()` sets values on the model instance
3. `validate(item, form_data, is_new)` is called
4. If `ValidationError` is raised, the form re-renders with values intact and a toast shows the error
5. If no error, the item is saved and the user is redirected

You can also raise `ValidationError` from `_apply_form_data()` if you need to validate during data transformation:

```python
class OfferingView(CRUDView):
    model = Offering

    def _apply_form_data(self, item, form_data):
        super()._apply_form_data(item, form_data)
        if item.serverid and not item.ipaddress:
            raise ValidationError("IP address is required when a server is selected")
```

---

## Progress Bar

fasthx-admin includes a built-in progress bar partial (`partials/progress_bar.html`) that uses HTMX auto-polling to show real-time deployment or task progress. The progress bar appears inline in the list table, polls the server every 500ms, and stops polling automatically when it reaches 100%.

### How it works

1. A **row action** button sends an HTMX POST to start the operation
2. The POST endpoint returns the `progress_bar.html` partial, which is inserted after the row (`hx-swap="afterend"`)
3. The progress bar contains `hx-get` and `hx-trigger="every 500ms"` -- HTMX auto-polls the server
4. Each poll response returns an updated progress bar (with a higher percentage)
5. When progress reaches 100%, the template removes the `hx-get`/`hx-trigger` attributes, stopping polling

### Step 1: Add instance state

Your view needs a dictionary to track in-progress operations. Set it **before** calling `super().__init__()`:

```python
from typing import Dict

class EdgeView(CRUDView):
    model = FortiEdge

    def __init__(self, templates):
        self.deploy_progress: Dict[int, dict] = {}  # {item_id: {"progress": int, "status": str}}
        super().__init__(templates)
```

### Step 2: Add a row action button

Configure a "Deploy" button that triggers the operation. The key settings are `hx_swap: "afterend"` and `hx_target: "closest tr"` -- this inserts the progress bar as a new row directly below the clicked row.

```python
class EdgeView(CRUDView):
    model = FortiEdge
    row_actions = [
        {
            "label": "Deploy",
            "icon": "rocket",
            "hx_post": "/edges/{id}/deploy",   # {id} is replaced with the row's primary key
            "hx_swap": "afterend",             # Insert the progress bar AFTER this row
            "hx_target": "closest tr",         # Target the current table row
            "class": "btn-outline-success",
        },
    ]
```

### Step 3: Create the deploy endpoint

This endpoint starts the operation, initializes tracking state, and returns the initial progress bar (at 0%). Use `get_colspan()` to make the progress bar span the full table width.

```python
import time
from fastapi import Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from fasthx_admin import get_db

class EdgeView(CRUDView):
    model = FortiEdge

    def setup_endpoints(self):
        view = self
        model = self.model
        templates = self.templates

        @self.router.post(f"/{self.name}/{{item_id}}/deploy", response_class=HTMLResponse)
        async def deploy(request: Request, item_id: int, db: Session = Depends(get_db)):
            # 1. Update the database record
            item = db.query(model).filter(model.id == item_id).first()
            if not item:
                return HTMLResponse("Not found", status_code=404)
            item.status = "deploying"
            item.deploy_progress = 0
            db.commit()

            # 2. Initialize in-memory progress tracking
            view.deploy_progress[item_id] = {
                "progress": 0,
                "status": "deploying",
                "started": time.time(),
            }

            # 3. Return the progress bar partial (starts auto-polling)
            return templates.TemplateResponse("partials/progress_bar.html", {
                "request": request,
                "edge_id": item_id,
                "progress": 0,
                "status": "Starting...",
                "colspan": view.get_colspan(),  # Spans all table columns
            })
```

### Step 4: Create the progress polling endpoint

This endpoint is called automatically by HTMX every 500ms. It increments the progress, and when done, updates the database and marks the operation complete.

```python
        @self.router.get(f"/{self.name}/{{item_id}}/progress", response_class=HTMLResponse)
        async def progress(request: Request, item_id: int, db: Session = Depends(get_db)):
            state = view.deploy_progress.get(item_id, {"progress": 0, "status": "unknown"})

            # Increment progress (replace with your real logic)
            if state["progress"] < 100:
                state["progress"] = min(100, state["progress"] + random.randint(5, 15))
                view.deploy_progress[item_id] = state

            # When complete, update the database
            if state["progress"] >= 100:
                item = db.query(model).filter(model.id == item_id).first()
                if item:
                    item.status = "online"
                    item.deploy_progress = 100
                    db.commit()
                state["status"] = "Complete"

            return templates.TemplateResponse("partials/progress_bar.html", {
                "request": request,
                "edge_id": item_id,
                "progress": state["progress"],
                "status": state.get("status", "deploying"),
                "colspan": view.get_colspan(),
            })
```

### How the template works

The `partials/progress_bar.html` template renders a `<tr>` element with a Bootstrap progress bar inside. Here's what makes it tick:

```html
<tr class="progress-row" id="progress-{{ edge_id }}"
    {% if progress < 100 %}
    hx-get="/edges/{{ edge_id }}/progress"
    hx-trigger="every 500ms"
    hx-swap="outerHTML"
    {% endif %}>
    <td colspan="{{ colspan | default(8) }}">
        <!-- Progress bar with animated stripes while in progress -->
        <div class="progress" style="height: 20px;">
            <div class="progress-bar progress-bar-striped
                 {% if progress < 100 %}progress-bar-animated{% endif %}
                 {% if progress >= 100 %}bg-success{% endif %}"
                 style="width: {{ progress }}%">
                {{ progress }}%
            </div>
        </div>
        <!-- Status badge: "deploying" while running, "Complete" when done -->
        <span class="badge {% if progress >= 100 %}bg-success{% else %}bg-primary{% endif %}">
            {% if progress >= 100 %}Complete{% else %}{{ status }}{% endif %}
        </span>
    </td>
</tr>
```

Key details:
- **`hx-get` + `hx-trigger="every 500ms"`** -- HTMX polls the progress endpoint twice per second
- **`hx-swap="outerHTML"`** -- each poll response replaces the entire `<tr>`, updating the progress bar
- **`{% if progress < 100 %}`** -- when progress reaches 100%, the `hx-get` and `hx-trigger` attributes are omitted, which **stops polling automatically**
- **`progress-bar-animated`** -- Bootstrap's animated striped effect while in progress, removed on completion
- **`bg-success`** -- the bar turns green when complete
- **`colspan`** -- spans all table columns so the progress bar stretches across the full row width

### Template variables

| Variable | Type | Description |
|---|---|---|
| `edge_id` | `int` | The item's primary key (used in the polling URL and element ID) |
| `progress` | `int` | Current progress percentage (0-100) |
| `status` | `str` | Status text shown in the badge (e.g. "deploying", "Complete") |
| `colspan` | `int` | Number of table columns to span (use `view.get_colspan()`) |

### Optional: Reset / cleanup

Add a reset endpoint to cancel or clean up after a deployment:

```python
        @self.router.post(f"/{self.name}/{{item_id}}/reset", response_class=HTMLResponse)
        async def reset(request: Request, item_id: int, db: Session = Depends(get_db)):
            item = db.query(model).filter(model.id == item_id).first()
            if not item:
                return HTMLResponse("Not found", status_code=404)
            item.status = "pending"
            item.deploy_progress = 0
            db.commit()
            # Remove from in-memory tracking
            view.deploy_progress.pop(item_id, None)
            # Redirect to refresh the list page
            return HTMLResponse("", headers={"HX-Redirect": f"/{view.name}"})
```

### Using the progress bar in a wizard

The progress bar can also be used outside list tables. In the demo's deploy wizard, step 4 triggers a deployment and shows progress inline:

```python
# In the wizard step handler, start deployment:
edge_view = admin.get_view("edges")
edge_view.deploy_progress[edge_id] = {
    "progress": 0,
    "status": "deploying",
    "started": time.time(),
}

# Then poll a separate wizard-specific endpoint:
@app.get("/wizard/deploy-status/{edge_id}", response_class=HTMLResponse)
async def wizard_deploy_status(request: Request, edge_id: int, db: Session = Depends(get_db)):
    state = admin.get_view("edges").deploy_progress.get(edge_id, {"progress": 0})

    if state["progress"] < 100:
        state["progress"] = min(100, state["progress"] + random.randint(3, 10))

    if state["progress"] >= 100:
        # Update DB and show completion UI
        return HTMLResponse("""
            <div class="text-center">
                <i class="bi bi-check-circle-fill text-success" style="font-size: 3rem;"></i>
                <h5 class="mt-2 text-success">Deployment Complete!</h5>
            </div>
        """)

    # Show inline progress (polled via hx-get + hx-trigger="every 1s")
    return HTMLResponse(f"""
        <div hx-get="/wizard/deploy-status/{edge_id}" hx-trigger="every 1s" hx-swap="outerHTML">
            <div class="progress" style="height: 20px;">
                <div class="progress-bar progress-bar-striped progress-bar-animated"
                     style="width: {state['progress']}%">{state['progress']}%</div>
            </div>
        </div>
    """)
```

### Replacing simulated progress with real logic

The demo uses `random.randint(5, 15)` to simulate progress. In a real application, replace this with actual task tracking:

```python
# Example: Track a background task
import asyncio

async def real_deploy(item_id: int, view: EdgeView):
    """Run the actual deployment steps and update progress."""
    steps = ["Uploading config", "Applying policies", "Verifying connectivity"]
    for i, step in enumerate(steps):
        await do_deployment_step(item_id, step)  # Your real logic
        progress = int((i + 1) / len(steps) * 100)
        view.deploy_progress[item_id] = {"progress": progress, "status": step}

# In the deploy endpoint, kick off the background task:
@self.router.post(f"/{self.name}/{{item_id}}/deploy", response_class=HTMLResponse)
async def deploy(request: Request, item_id: int, db: Session = Depends(get_db)):
    view.deploy_progress[item_id] = {"progress": 0, "status": "Starting..."}
    asyncio.create_task(real_deploy(item_id, view))  # Runs in background
    return templates.TemplateResponse("partials/progress_bar.html", {
        "request": request,
        "edge_id": item_id,
        "progress": 0,
        "status": "Starting...",
        "colspan": view.get_colspan(),
    })
```

The polling endpoint then just reads the current state -- no need to increment it since the background task handles that.

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

## AI Chat (Optional)

fasthx-admin ships with an optional AI chat widget that adds a floating assistant to every page. It supports any OpenAI-compatible API (OpenAI, vLLM, Ollama, LiteLLM, etc.), a decorator-based tool registry so the AI can call your Python functions, and a settings UI stored in the database.

### Enabling AI Chat

Pass `ai_chat=True` when creating the Admin instance:

```python
from fasthx_admin import Admin

admin = Admin(app, title="My Admin", ai_chat=True)
```

This automatically:
- Creates a `fasthx_admin_ai_settings` table in your database
- Mounts chat API endpoints under `/ai/`
- Adds "AI Settings" and "AI Context & Tools" links in the sidebar under a "Settings" category
- Includes the chat widget on every page (once enabled in settings)

### Installing the AI Dependency

The AI chat uses `httpx` for async HTTP calls to the LLM API. Install it via the `ai` extra:

```bash
pip install fasthx-admin[ai]
```

If you already have `httpx` installed (e.g. from the `dev` extra), no additional install is needed.

### Registering Tools

Tools let the AI call your Python functions to answer questions with live data. Use the `@tool_registry.tool()` decorator:

```python
from fasthx_admin import tool_registry

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
    return "\n".join(f"- {c.name} (SID: {c.sid})" for c in results)

@tool_registry.tool(description="Get edge device statistics")
async def edge_stats(db=None):
    """Returns edge device status breakdown."""
    total = db.query(FortiEdge).count()
    return f"Total edges: {total}"
```

Key points:
- **`db` parameter** -- if your function accepts a `db` parameter, it automatically receives the current SQLAlchemy session
- **Async support** -- tools can be `async def` or regular `def`
- **Type hints** -- parameter types (str, int, float, bool) are extracted and sent to the AI in OpenAI function-calling format
- **Return a string** -- the return value is sent back to the AI as the tool result
- Tools must be **enabled** in the settings UI before the AI can use them

### Configuring via the Settings UI

After enabling `ai_chat=True`, navigate to **Settings > AI Settings** in the sidebar. The settings page has four sections:

| Section | Fields | Description |
|---|---|---|
| **General** | Enable/disable toggle | Master switch for the chat widget |
| **Connection** | Base URL, API key, model | Your LLM endpoint (e.g. `https://api.openai.com`, `http://localhost:11434`) |
| **Parameters** | Temperature, max tokens, timeout | Generation parameters |
| **System Prompt** | Large text area | Base instructions for the AI |

The **Context & Tools** page (linked from the settings page) lets you:
- Add **context items** -- named text segments injected into the system prompt (e.g. business rules, schema descriptions)
- Toggle context items on/off
- Enable/disable registered **tools** individually

All settings are stored in the `fasthx_admin_ai_settings` database table as key-value pairs.

### How It Works

```
User types message in chat widget
    → POST /ai/chat {message: "..."}
    → Load settings from DB (cached 30s)
    → Build system prompt (base + enabled context items)
    → Load session history (in-memory, keyed by cookie)
    → Call LLM provider with messages + enabled tools
    → If AI requests tool calls:
        → Execute tools via registry (with DB session)
        → Send tool results back to AI
        → Get final response
    → Save to session history (max 50 messages)
    → Return {response, tool_calls_made}
```

- **Session history** is stored in-memory on the server, keyed by a `fasthx_chat_sid` cookie
- History persists across page navigations but resets on server restart
- The chat widget renders markdown responses using `marked.js` + `DOMPurify` (loaded from CDN)
- Widget size and expanded/minimized state persist in `localStorage`

### Custom Providers

The built-in `OpenAICompatibleProvider` works with any API that speaks the OpenAI chat completions format. To integrate a different API, subclass `AIProvider`:

```python
from fasthx_admin import AIProvider

class MyCustomProvider(AIProvider):
    name = "my_provider"

    async def chat(self, messages, tools=None, **kwargs):
        # Call your LLM API here
        # Must return: {"response": str, "tool_calls": list | None}
        ...

    def get_config_fields(self):
        # Return list of settings fields for the UI
        return [
            {"key": "api_key", "label": "API Key", "type": "password", "default": ""},
        ]
```

### AI Chat API Endpoints

All endpoints are mounted under `/ai/`:

| Method | Path | Description |
|---|---|---|
| `POST` | `/ai/chat` | Send a message, get AI response (JSON) |
| `POST` | `/ai/clear` | Clear the current session's chat history |
| `GET` | `/ai/history` | Get the current session's message history (JSON) |
| `GET` | `/ai/settings` | AI settings page (HTML) |
| `POST` | `/ai/settings` | Save AI settings |
| `GET` | `/ai/settings/context` | Context & tools settings page (HTML) |
| `POST` | `/ai/settings/context` | Save context items and tool toggles |

The `POST /ai/chat` endpoint expects JSON `{"message": "..."}` and returns:

```json
{
    "response": "The AI's markdown response",
    "tool_calls": [
        {"name": "customer_count", "arguments": {}, "result": "There are 4 customers."}
    ]
}
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
| `form_ajax_refs` | `form_ajax_refs` | Same concept; uses HTMX instead of Select2 |
| `column_extra_row_actions` | `row_actions` | List of dicts with HTMX attrs |
| `@expose()` custom endpoints | `setup_endpoints()` override | Define on `self.router` |
| `Markup()` in formatters | Raw HTML strings | Templates use `\| safe` filter |

---

## Running the Demo

The package includes a full demo application in `examples/demo/`:

```bash
git clone https://github.com/talbiston/fasthx-admin.git
cd fasthx-admin
pip install -e .[dev]       # install from project root
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
| AI Chat | [httpx](https://www.python-httpx.org/) (optional `[ai]` extra) + [marked.js](https://marked.js.org/) / [DOMPurify](https://github.com/cure53/DOMPurify) (CDN) |
| Server | [Uvicorn](https://www.uvicorn.org/) (dev dependency) |
| JavaScript | Minimal -- theme toggle + HTMX event hooks + AI chat widget |
