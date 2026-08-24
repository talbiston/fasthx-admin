"""
Multi-step wizards.

``WizardView`` is to a multi-step form what ``CRUDView`` is to a single one:
declare the steps, and the routes, navigation, progress indicators, per-step
validation and the final save are generated for you.

    class OnboardWizard(WizardView):
        name = "onboard"
        model = Customer
        steps = [
            {"label": "Details", "fields": ["name", "sid"]},
            {"label": "Contact", "fields": ["email"]},
            {"label": "Review", "review": True},
        ]

    admin.add_view(OnboardWizard)

State lives in the form itself — every value collected so far is re-emitted as
a hidden input on the next step — so wizards are stateless on the server and
work across multiple workers with no session storage.

Set ``audit_log = True`` (with ``Admin(audit_logger=...)``) to record completions:
the default save emits the same ``"create"`` event a CRUD form would, and a
wizard whose ``on_finish`` returned its own Response emits ``"finish"`` carrying
what the user submitted. Password fields are masked in both, and on the review
step.
"""

from __future__ import annotations

import json
from inspect import isawaitable
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_current_user
from .crud import (
    ValidationError,
    _AuditMixin,
    _build_form_fields,
    _coerce_column_value,
    _depends_on_holds,
    _fk_choices,
    _has_form_value,
    _introspect_foreign_keys,
    _model_registry,
    _normalize_depends_on,
    toast_response,
)
from .database import get_db

#: Form key the wizard uses to track which step was submitted. Never user data.
STEP_KEY = "_step"

#: Stand-in shown wherever a password field's value would otherwise be printed.
MASK = "\u2022" * 8


def _form_to_dict(form) -> Dict[str, Any]:
    """Flatten a Starlette FormData into a dict, keeping repeated keys as lists."""
    data: Dict[str, Any] = {}
    for key, value in form.multi_items():
        if key in data:
            if isinstance(data[key], list):
                data[key].append(value)
            else:
                data[key] = [data[key], value]
        else:
            data[key] = value
    return data


def _virtual_fields(keys, column_labels, form_widget_overrides) -> List[dict]:
    """Build field metadata for a wizard with no model — every field is virtual."""
    fields = []
    for key in keys:
        override = form_widget_overrides.get(key, {})
        field = {
            "key": key,
            "label": override.get(
                "label", column_labels.get(key, key.replace("_", " ").title())
            ),
            "type": override.get("type", "text"),
            "required": override.get("required", False),
            "choices": override.get("choices"),
            "is_fk": False,
            "virtual": True,
        }
        field.update(override)
        _normalize_depends_on(field)
        fields.append(field)
    return fields


class WizardView(_AuditMixin):
    """
    Generates a multi-step form from a list of step definitions.

    Subclass and set class-level attributes, then register it the same way as a
    CRUDView::

        class DeployWizard(WizardView):
            name = "deploy"
            model = Edge
            steps = [
                {"label": "Device", "fields": ["hostname", "customer_id"]},
                {"label": "Network", "fields": ["wan_ip", "wan_gateway"]},
                {"label": "Review", "review": True},
            ]

        admin.add_view(DeployWizard)

    Each entry in ``steps`` is a dict:

    ==============  ==========================================================
    ``label``       Text under the step's circle in the progress indicator.
    ``fields``      Column names (or virtual field keys) to collect on this
                    step. Rendered with the same widgets as a CRUD form.
    ``title``       Heading above the fields. Defaults to ``label``.
    ``description`` Muted text under the heading.
    ``review``      ``True`` renders a read-only summary of everything
                    collected so far instead of fields.
    ``template``    Custom template for the step body. It is rendered inside
                    the wizard frame, so hidden state, the Back/Next buttons
                    and the indicators still come for free.
    ``nav``         ``False`` hides the Back/Next buttons (for a final
                    "working..." step that drives itself).
    ==============  ==========================================================
    """

    # --- Class-level config (override in subclasses) ---
    #: URL slug — the wizard lives at ``/{name}``. Required.
    name = None
    #: Optional SQLAlchemy model. When set, step fields may name its columns and
    #: the default ``on_finish`` creates a row from the collected data.
    model = None
    display_name = None
    description = None
    category = "Wizards"
    icon = "magic"
    steps: List[dict] = []
    column_labels = None
    form_widget_overrides = None
    #: Label/icon of the button on the last step.
    finish_label = "Finish"
    finish_busy_label = "Working…"
    finish_icon = "check-lg"
    #: Where to send the user after a successful finish. Defaults to the model's
    #: list view, or back to the wizard when there is no model.
    finish_redirect = None
    success_message = "Completed successfully"
    allowed_users = None
    allowed_groups = None
    #: Emit an audit event when the wizard finishes (needs Admin(audit_logger=...)).
    audit_log = False
    audit_log_exclude = None
    pk_field = "id"
    wizard_template = "wizard.html"

    def __init__(self, templates):
        if not self.name:
            raise ValueError(f"{type(self).__name__} must define a 'name' attribute")
        if not self.steps:
            raise ValueError(f"{type(self).__name__} must define at least one step")

        self.templates = templates
        self.display_name = self.display_name or self.name.replace("_", " ").replace("-", " ").title()
        self.column_labels = self.column_labels or {}
        self.form_widget_overrides = self.form_widget_overrides or {}

        self.foreign_keys = (
            _introspect_foreign_keys(self.model) if self.model is not None else {}
        )
        if self.model is not None:
            _model_registry.setdefault(self.model.__tablename__, self.model)

        # Resolve each step's fields into renderable field metadata.
        self.steps_meta: List[dict] = []
        for index, step in enumerate(self.steps, start=1):
            keys = list(step.get("fields") or [])
            fields = self._resolve_fields(keys, type(self).__name__, index)
            self.steps_meta.append({
                "index": index,
                "label": step.get("label", f"Step {index}"),
                "title": step.get("title", step.get("label", f"Step {index}")),
                "description": step.get("description"),
                "review": bool(step.get("review")),
                "template": step.get("template"),
                "nav": step.get("nav", True),
                "field_keys": keys,
                "fields": fields,
            })

        # Every field across every step, in order — what the default save writes.
        self.all_fields = [f for meta in self.steps_meta for f in meta["fields"]]

        # Password fields are masked in the review step and kept out of audit
        # payloads. (Their value still round-trips through the form's hidden
        # inputs — that is how wizard state is carried — so treat the page
        # source the same way you would any form carrying a secret.)
        self.sensitive_keys = {
            f["key"] for f in self.all_fields if f.get("type") == "password"
        }

        self.router = APIRouter()
        self._setup_routes()
        self.setup_endpoints()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def _resolve_fields(self, keys: List[str], cls_name: str, index: int) -> List[dict]:
        """Turn a step's field keys into field metadata dicts."""
        if not keys:
            return []
        if self.model is None:
            return _virtual_fields(keys, self.column_labels, self.form_widget_overrides)

        fields = _build_form_fields(
            self.model,
            keys,
            column_labels=self.column_labels,
            form_widget_overrides=self.form_widget_overrides,
            foreign_keys=self.foreign_keys,
        )
        # A key that is neither a model column nor an override is a typo, and
        # silently dropping it would leave a step mysteriously missing a field.
        missing = [k for k in keys if k not in {f["key"] for f in fields}]
        if missing:
            raise ValueError(
                f"{cls_name} step {index} references unknown field(s) {missing}. "
                f"Add them as columns on {self.model.__name__} or define them in "
                f"form_widget_overrides."
            )
        return fields

    def _prepare_fields(self, db: Session, meta: dict, data: dict) -> List[dict]:
        """Copy a step's fields and fill in current values and select choices."""
        prepared = []
        for field in meta["fields"]:
            f = dict(field)
            f["value"] = data.get(field["key"])
            choices = f.get("choices")
            if callable(choices):
                f["choices"] = choices(db)
            elif field.get("is_fk") and not choices:
                f["choices"] = _fk_choices(db, self.foreign_keys, field["key"])
            prepared.append(f)
        return prepared

    def _display_value(self, db: Session, field: dict, value) -> str:
        """Human-readable value for the review step."""
        if value is None or value == "":
            return "—"
        if field.get("type") == "password":
            return MASK
        if field.get("type") == "checkbox":
            return "Yes" if str(value) in ("true", "1", "on", "True") else "No"
        choices = field.get("choices")
        if callable(choices):
            choices = choices(db)
        if choices:
            for val, label in choices:
                if str(val) == str(value):
                    return label
        if field.get("is_fk"):
            return self._fk_label(db, field["key"], value)
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)

    def _fk_label(self, db: Session, key: str, value) -> str:
        """``str()`` of the row a foreign key value points at."""
        fk = self.foreign_keys.get(key)
        if not fk:
            return str(value)
        target_model = _model_registry.get(fk.column.table.name)
        if target_model is None:
            return str(value)
        pk_attr = getattr(target_model, fk.column.key, None)
        if pk_attr is None:
            pk_attr = getattr(target_model, "id", None)
        if pk_attr is None:
            return str(value)
        row = db.query(target_model).filter(pk_attr == value).first()
        return str(row) if row is not None else str(value)

    def _review_rows(self, db: Session, data: dict) -> List[dict]:
        """Label/value pairs for every field collected before the review step."""
        rows = []
        for field in self.all_fields:
            key = field["key"]
            if key not in data:
                continue
            rows.append({
                "label": field.get("label") or key.replace("_", " ").title(),
                "value": self._display_value(db, field, data.get(key)),
            })
        return rows

    def _redact(self, data: dict) -> dict:
        """Copy of *data* with every password field's value masked."""
        if not self.sensitive_keys:
            return dict(data)
        return {
            key: (MASK if key in self.sensitive_keys and value else value)
            for key, value in data.items()
        }

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(
        self,
        request: Request,
        db: Session,
        index: int,
        data: dict,
        *,
        full_page: bool = False,
        error: str | None = None,
    ):
        """Render one step — the whole page on GET, just the step body on HTMX."""
        meta = self.steps_meta[index - 1]
        form_fields = self._prepare_fields(db, meta, data)

        # Values the user can edit on this step render as real inputs; everything
        # else collected so far rides along as hidden inputs.
        visible = {f["key"] for f in form_fields}
        state_fields = [
            (key, value)
            for key, value in data.items()
            if key not in visible and key != STEP_KEY
        ]

        context = {
            "request": request,
            "wizard": self,
            "wizard_steps": self.steps_meta,
            "step": index,
            "step_meta": meta,
            "data": data,
            "form_fields": form_fields,
            "state_fields": state_fields,
            "review_rows": self._review_rows(db, data) if meta["review"] else [],
            "is_last": index == len(self.steps_meta),
            # The step partial carries an out-of-band header swap; the full page
            # already renders the header itself and must not repeat it.
            "is_partial": not full_page,
            "active_page": self.name,
        }

        template = self.wizard_template if full_page else "partials/_wizard_step.html"
        headers = None
        if error:
            headers = {
                "HX-Trigger": json.dumps({"showToast": {
                    "message": error, "type": "danger", "title": "Validation Error",
                }})
            }
        return self.templates.TemplateResponse(template, context, headers=headers)

    # ------------------------------------------------------------------
    # Validation and saving
    # ------------------------------------------------------------------

    def _validate_step(self, meta: dict, data: dict):
        """Raise ValidationError for any required field this step left empty."""
        for field in meta["fields"]:
            if not field.get("required") or field.get("type") == "checkbox":
                continue
            if not _depends_on_holds(field, data):
                continue
            if not _has_form_value(data, field["key"]):
                label = field.get("label") or field["key"].replace("_", " ").title()
                raise ValidationError(f"{label} is required")

    def apply_data(self, item, data: dict):
        """Write collected wizard data onto a model instance (no commit).

        Values are coerced to each column's type the same way CRUD forms do.
        Virtual fields and keys that are not columns are skipped — read those
        straight off ``data`` in ``on_finish``.
        """
        if self.model is None:
            raise ValueError(f"{type(self).__name__} has no model to apply data to")
        mapper = sa_inspect(self.model)
        for field in self.all_fields:
            key = field["key"]
            if field.get("virtual"):
                continue
            col = mapper.columns.get(key)
            if col is None:
                continue
            if key in data:
                setattr(item, key, _coerce_column_value(col, data[key]))
            elif type(col.type).__name__.upper() == "BOOLEAN":
                # Unchecked checkboxes are never submitted.
                setattr(item, key, False)

    def _default_finish(self, data: dict, db: Session, request: Request):
        """Create a model row from the collected data, or just say 'done'."""
        if self.model is None:
            self._audit_finish(data, request)
            return toast_response(
                self.success_message,
                type="success",
                redirect=self.finish_redirect or f"/{self.name}",
            )
        item = self.model()
        self.apply_data(item, data)
        db.add(item)
        db.commit()
        db.refresh(item)
        self.after_finish(item, data, db, request)
        # Same action name and payload shape as a CRUD create, so a wizard-made
        # row and a form-made row look alike in the audit trail.
        self._audit_emit(
            action="create",
            item=item,
            request=request,
            data=self._redact(self._audit_snapshot(item)),
        )
        redirect = self.finish_redirect or f"/{self.model.__tablename__}"
        return toast_response(self.success_message, type="success", redirect=redirect)

    def _audit_finish(self, data: dict, request: Request, item=None) -> None:
        """Audit a wizard that completed without the default save.

        The collected form data *is* the record of what happened when there is
        no row to snapshot, so it becomes the payload — password fields masked.
        """
        self._audit_emit(
            action="finish",
            item=item,
            request=request,
            data=self._redact(data),
        )

    # ------------------------------------------------------------------
    # Hooks (override in subclasses)
    # ------------------------------------------------------------------

    def on_step(self, step: int, data: dict, db: Session, request: Request = None):
        """Called when moving forward off ``step`` (1-based), after its required
        fields pass. Raise ``ValidationError`` to send the user back to the step
        with an error toast. Mutating ``data`` here carries values forward.
        """
        pass

    def on_finish(self, data: dict, db: Session, request: Request = None):
        """Called when the last step is submitted.

        Return a Response to take over completely, or ``None`` to get the
        default behaviour: create a ``model`` row from the collected data and
        redirect to its list view. May be ``async``.
        """
        return None

    def after_finish(self, item, data: dict, db: Session, request: Request = None):
        """Called after the default finish committed ``item``. Side effects only."""
        pass

    def setup_endpoints(self):
        """Override to register extra routes on ``self.router``."""
        pass

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _check_access(self, request: Request):
        """Return a 403 response if the current user is not allowed, else None."""
        if not self.allowed_users and not self.allowed_groups:
            return None
        from .crud import Admin

        if Admin._user_allowed(
            get_current_user(request), self.allowed_users, self.allowed_groups
        ):
            return None
        return HTMLResponse("Access denied", status_code=403)

    def _setup_routes(self):
        wizard = self
        last = len(self.steps_meta)

        @self.router.get(f"/{self.name}", response_class=HTMLResponse)
        def wizard_start(request: Request, db: Session = Depends(get_db)):
            denied = wizard._check_access(request)
            if denied:
                return denied
            return wizard._render(request, db, 1, {}, full_page=True)

        @self.router.post(f"/{self.name}/step/{{index}}", response_class=HTMLResponse)
        async def wizard_step(
            request: Request, index: int, db: Session = Depends(get_db)
        ):
            denied = wizard._check_access(request)
            if denied:
                return denied
            if index < 1 or index > last:
                return HTMLResponse("No such step", status_code=404)

            data = _form_to_dict(await request.form())
            current = int(data.pop(STEP_KEY, 0) or 0)

            # Only moving forward validates; going back must never block.
            if index > current >= 1:
                try:
                    wizard._validate_step(wizard.steps_meta[current - 1], data)
                    wizard.on_step(current, data, db, request)
                except ValidationError as exc:
                    db.rollback()
                    return wizard._render(
                        request, db, current, data, error=exc.message
                    )
            return wizard._render(request, db, index, data)

        @self.router.post(f"/{self.name}/finish", response_class=HTMLResponse)
        async def wizard_finish(request: Request, db: Session = Depends(get_db)):
            denied = wizard._check_access(request)
            if denied:
                return denied

            data = _form_to_dict(await request.form())
            current = int(data.pop(STEP_KEY, last) or last)
            meta = wizard.steps_meta[max(1, min(current, last)) - 1]

            try:
                wizard._validate_step(meta, data)
                result = wizard.on_finish(data, db, request)
                if isawaitable(result):
                    result = await result
                if isinstance(result, Response):
                    # on_finish took over — there is no row to snapshot, so log
                    # what the user submitted. Call self.audit() from inside
                    # on_finish when you want a richer payload than this.
                    wizard._audit_finish(data, request)
                    return result
                return wizard._default_finish(data, db, request)
            except ValidationError as exc:
                db.rollback()
                return wizard._render(request, db, meta["index"], data, error=exc.message)
            except IntegrityError:
                db.rollback()
                return wizard._render(
                    request,
                    db,
                    meta["index"],
                    data,
                    error="Database constraint failed — a value may already be in use.",
                )

    def register(self, app):
        """Register this wizard's routes with the FastAPI app."""
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
