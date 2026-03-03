# fasthx-admin

FastAPI + HTMX + Jinja2 admin interface framework — a modern replacement for Flask-Admin.

## Features

- Auto-generated CRUD routes from SQLAlchemy models
- Dark/light theme with Bootstrap 5.3
- HTMX-powered interactions (search, sorting, polling, dependent dropdowns)
- Accordion-grouped form sections
- Custom column formatters and row actions
- OIDC/Keycloak authentication (with `AUTH_DISABLED` dev mode)
- Deploy wizard with real-time progress tracking
- Responsive sidebar navigation

## Installation

```bash
pip install fasthx-admin
```

For development (includes uvicorn):

```bash
pip install fasthx-admin[dev]
```

## Quick Start

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from fasthx_admin import Admin, CRUDView, Base, init_db

# 1. Define your models
from sqlalchemy import Column, Integer, String

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    sid = Column(String(50), nullable=False)

    __admin_category__ = "CRM"
    __admin_icon__ = "building"
    __admin_name__ = "Customers"

# 2. Initialise the database
engine = init_db("sqlite:///./app.db", connect_args={"check_same_thread": False})

# 3. Create the app
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key="change-me")

# 4. Create the admin and register views
admin = Admin(app, title="My Admin")

class CustomerView(CRUDView):
    model = Customer
    column_list = ["id", "name", "sid"]

admin.add_view(CustomerView)
```

Run with auth disabled for development:

```bash
AUTH_DISABLED=1 uvicorn app:app --reload
```

## CRUDView Configuration

Subclass `CRUDView` and set class-level attributes:

| Attribute | Description |
|---|---|
| `model` | SQLAlchemy model class (required) |
| `name` | URL prefix (defaults to `__tablename__`) |
| `display_name` | Sidebar label (defaults to model's `__admin_name__`) |
| `category` | Sidebar group (defaults to model's `__admin_category__`) |
| `icon` | Bootstrap Icons name (defaults to model's `__admin_icon__`) |
| `column_list` | Columns to show in the list view |
| `column_exclude` | Columns to exclude (alternative to `column_list`) |
| `column_labels` | Display name overrides, e.g. `{"customer_id": "Customer"}` |
| `column_formatters` | `{col: fn(value, obj) -> html_string}` |
| `column_searchable` | Columns to search (defaults to all String columns) |
| `column_sortable` | Columns that can be sorted |
| `form_columns` | Editable fields (defaults to all except `id`) |
| `form_sections` | Accordion groups: `{"Section": ["field1", "field2"]}` |
| `form_widget_overrides` | Per-field HTMX attrs or select choices |
| `row_actions` | Custom action buttons per row |
| `htmx_columns` | Auto-polling cells: `{"field": {"url": "...", "trigger": "every 3s"}}` |
| `page_size` | Records per page (default 20) |
| `can_create` / `can_edit` / `can_delete` | Permission flags |

## Custom Endpoints

Override `setup_endpoints()` on your CRUDView subclass to add custom routes:

```python
class OrchestratorView(CRUDView):
    model = Orchestrator

    def setup_endpoints(self):
        @self.router.post(f"/{self.name}/{{item_id}}/build")
        async def build(request: Request, item_id: int, db=Depends(get_db)):
            # custom logic
            return HTMLResponse("", headers={"HX-Redirect": f"/{self.name}"})
```

## Admin Class

```python
admin = Admin(
    app,
    title="My Admin",           # Sidebar brand + page titles
    static_url="/static/fasthx-admin",  # Where package statics are mounted
    public_pages={"login.html"},        # Pages that skip auth check
)
```

The `Admin` instance:
- Mounts built-in static files (CSS, JS)
- Sets up Jinja2 templates from the package
- Wraps template responses with nav context + auth check
- Provides `admin.templates` for rendering custom pages

## Model Metadata

Set these on your SQLAlchemy model classes for automatic sidebar grouping:

```python
class MyModel(Base):
    __tablename__ = "my_models"
    __admin_category__ = "Section Name"  # Sidebar group
    __admin_icon__ = "table"             # Bootstrap Icons name
    __admin_name__ = "My Models"         # Display label
```

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `AUTH_DISABLED` | Bypass authentication (`1`/`true`/`yes`) | disabled |
| `SESSION_SECRET` | Session signing key | (set in your app) |
| `OIDC_SECRETS` | Path to `client_secrets.json` | `./client_secrets.json` |

## Running the Demo

```bash
cd examples/demo
pip install -e ../..
pip install uvicorn[standard]
AUTH_DISABLED=1 uvicorn app:app --reload
```

Open http://127.0.0.1:8000
