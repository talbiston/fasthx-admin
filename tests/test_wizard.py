"""Tests for WizardView — step navigation, validation, state carry and saving.

The demo app exercises the model-less path (a wizard that drives a deployment);
these cover the model-backed path, where the wizard's last step saves a row.
"""

import json
import os

# Must be set before importing fasthx_admin.auth (it reads the env at import).
os.environ["AUTH_DISABLED"] = "1"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.pool import StaticPool

from fasthx_admin import Admin, CRUDView, ValidationError, WizardView, toast_response
from fasthx_admin.database import Base, get_db, init_db
from fasthx_admin.wizard import MASK


class Team(Base):
    __tablename__ = "team"
    id = Column(Integer, primary_key=True)
    name = Column(String)

    def __str__(self):
        return self.name


class Member(Base):
    __tablename__ = "member"
    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False)
    email = Column(String, nullable=False)
    nickname = Column(String, nullable=True)
    secret = Column(String, nullable=True)
    team_id = Column(Integer, ForeignKey("team.id"))
    team = relationship("Team")


class MemberView(CRUDView):
    model = Member
    name = "member"


class OnboardWizard(WizardView):
    name = "onboard"
    model = Member
    success_message = "Member onboarded"
    steps = [
        {"label": "Account", "fields": ["username", "email"]},
        {"label": "Team", "fields": ["team_id", "nickname"]},
        {"label": "Review", "review": True},
    ]

    def on_step(self, step, data, db, request=None):
        if step == 1 and "@" not in data.get("email", ""):
            raise ValidationError("Email must contain @")


@pytest.fixture()
def client():
    engine = init_db(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # single shared in-memory connection
    )
    Base.metadata.create_all(engine)

    db = next(get_db())
    db.add(Team(id=1, name="Platform"))
    db.commit()
    db.close()

    app = FastAPI()
    admin = Admin(app)
    admin.add_view(MemberView)
    admin.add_view(OnboardWizard)
    yield TestClient(app)

    Base.metadata.drop_all(engine)


def _toast(resp) -> str:
    trigger = resp.headers.get("HX-Trigger")
    return json.loads(trigger)["showToast"]["message"] if trigger else ""


def test_first_step_renders_its_fields_and_all_indicators(client):
    resp = client.get("/onboard")
    assert resp.status_code == 200
    assert 'name="username"' in resp.text
    assert 'name="email"' in resp.text
    # Later steps' fields are not on the page yet...
    assert 'name="nickname"' not in resp.text
    # ...but every step has an indicator.
    for label in ("Account", "Team", "Review"):
        assert label in resp.text


def test_step_advances_and_carries_state_as_hidden_inputs(client):
    resp = client.post(
        "/onboard/step/2",
        data={"_step": "1", "username": "ada", "email": "ada@example.com"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert '<input type="hidden" name="username" value="ada">' in resp.text
    assert '<input type="hidden" name="email" value="ada@example.com">' in resp.text
    assert 'name="nickname"' in resp.text
    # Out-of-band swap keeps the header indicators in sync.
    assert 'hx-swap-oob="innerHTML"' in resp.text


def test_missing_required_field_bounces_back_with_a_toast(client):
    resp = client.post(
        "/onboard/step/2",
        data={"_step": "1", "username": "ada", "email": ""},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert _toast(resp) == "Email is required"
    # Re-renders step 1, keeping what was typed.
    assert 'id="wizard-step-1"' in resp.text
    assert 'value="ada"' in resp.text


def test_on_step_validation_error_bounces_back(client):
    resp = client.post(
        "/onboard/step/2",
        data={"_step": "1", "username": "ada", "email": "not-an-email"},
        headers={"HX-Request": "true"},
    )
    assert _toast(resp) == "Email must contain @"
    assert 'id="wizard-step-1"' in resp.text


def test_going_back_never_validates(client):
    """Back must work even when the current step is empty."""
    resp = client.post(
        "/onboard/step/1",
        data={"_step": "2", "username": "ada", "email": "ada@example.com", "team_id": ""},
        headers={"HX-Request": "true"},
    )
    assert "HX-Trigger" not in resp.headers
    assert 'id="wizard-step-1"' in resp.text
    # Values already collected come back pre-filled.
    assert 'value="ada"' in resp.text


def test_review_step_shows_labels_and_resolved_fk(client):
    resp = client.post(
        "/onboard/step/3",
        data={
            "_step": "2",
            "username": "ada",
            "email": "ada@example.com",
            "team_id": "1",
            "nickname": "Countess",
        },
        headers={"HX-Request": "true"},
    )
    assert "ada@example.com" in resp.text
    assert "Countess" in resp.text
    # FK renders as the related row's str(), not its id.
    assert "Platform" in resp.text


def test_finish_saves_the_row_and_redirects(client):
    resp = client.post(
        "/onboard/finish",
        data={
            "_step": "3",
            "username": "ada",
            "email": "ada@example.com",
            "team_id": "1",
            "nickname": "Countess",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/member"

    db = next(get_db())
    try:
        member = db.query(Member).filter(Member.username == "ada").one()
        assert member.email == "ada@example.com"
        assert member.nickname == "Countess"
        # Coerced to int, not left as the string "1".
        assert member.team_id == 1
    finally:
        db.close()


def test_finish_still_enforces_required_fields(client):
    resp = client.post(
        "/onboard/finish",
        data={"_step": "1", "username": "", "email": ""},
        headers={"HX-Request": "true"},
    )
    assert _toast(resp) == "Username is required"
    db = next(get_db())
    try:
        assert db.query(Member).count() == 0
    finally:
        db.close()


def test_wizard_appears_in_the_sidebar(client):
    resp = client.get("/member")
    assert 'href="/onboard"' in resp.text
    assert "Wizards" in resp.text


def test_unknown_step_index_is_404(client):
    resp = client.post("/onboard/step/9", data={"_step": "1"})
    assert resp.status_code == 404


def test_unknown_field_key_fails_loudly_at_startup():
    class BrokenWizard(WizardView):
        name = "broken"
        model = Member
        steps = [{"label": "Oops", "fields": ["not_a_column"]}]

    app = FastAPI()
    admin = Admin(app)
    with pytest.raises(ValueError, match="not_a_column"):
        admin.add_view(BrokenWizard)


def test_wizard_without_a_name_fails_loudly():
    class NamelessWizard(WizardView):
        model = Member
        steps = [{"label": "One", "fields": ["username"]}]

    app = FastAPI()
    admin = Admin(app)
    with pytest.raises(ValueError, match="name"):
        admin.add_view(NamelessWizard)


# ---------------------------------------------------------------------------
# Audit logging + password masking
# ---------------------------------------------------------------------------

AUDIT_EVENTS = []


class SignupWizard(WizardView):
    """Model-backed, audited, with a password field."""

    name = "signup"
    model = Member
    audit_log = True
    steps = [
        {"label": "Account", "fields": ["username", "email", "secret"]},
        {"label": "Review", "review": True},
    ]
    form_widget_overrides = {"secret": {"type": "password", "label": "Secret"}}


class ActionWizard(WizardView):
    """No model, and on_finish takes over — there is no row to snapshot."""

    name = "action"
    audit_log = True
    steps = [
        {"label": "Input", "fields": ["target", "token"]},
        {"label": "Review", "review": True},
    ]
    form_widget_overrides = {
        "target": {"required": True},
        "token": {"type": "password"},
    }

    def on_finish(self, data, db, request=None):
        return toast_response("Done", type="success")


class QuietWizard(WizardView):
    """audit_log left at its default — must stay silent."""

    name = "quiet"
    model = Member
    steps = [{"label": "Account", "fields": ["username", "email"]}]


@pytest.fixture()
def audit_client():
    engine = init_db(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    AUDIT_EVENTS.clear()

    app = FastAPI()
    admin = Admin(app, audit_logger=AUDIT_EVENTS.append)
    admin.add_view(MemberView)
    admin.add_view(SignupWizard)
    admin.add_view(ActionWizard)
    admin.add_view(QuietWizard)
    yield TestClient(app)

    Admin.audit_logger = None
    Base.metadata.drop_all(engine)


def test_finish_emits_a_create_audit_event(audit_client):
    audit_client.post(
        "/signup/finish",
        data={"_step": "2", "username": "ada", "email": "ada@example.com", "secret": "hunter2"},
        headers={"HX-Request": "true"},
    )
    assert len(AUDIT_EVENTS) == 1
    event = AUDIT_EVENTS[0]
    # Same shape a CRUD create emits, so both look alike in the trail.
    assert event["action"] == "create"
    assert event["model_name"] == "Member"
    assert event["view_name"] == "signup"
    assert event["item_id"] is not None
    assert event["data"]["email"] == "ada@example.com"


def test_password_is_masked_in_the_audit_payload(audit_client):
    audit_client.post(
        "/signup/finish",
        data={"_step": "2", "username": "ada", "email": "ada@example.com", "secret": "hunter2"},
        headers={"HX-Request": "true"},
    )
    data = AUDIT_EVENTS[0]["data"]
    assert data["secret"] == MASK
    assert "hunter2" not in str(data)

    # Masking is for the trail only — the real value still reached the row.
    db = next(get_db())
    try:
        assert db.query(Member).one().secret == "hunter2"
    finally:
        db.close()


def test_password_is_masked_in_the_review_step(audit_client):
    resp = audit_client.post(
        "/signup/step/2",
        data={"_step": "1", "username": "ada", "email": "ada@example.com", "secret": "hunter2"},
        headers={"HX-Request": "true"},
    )
    assert MASK in resp.text
    # The review table must not print it...
    assert ">hunter2<" not in resp.text
    # ...but it still rides along in the hidden input that carries wizard state.
    assert 'name="secret" value="hunter2"' in resp.text


def test_custom_on_finish_emits_a_finish_event(audit_client):
    audit_client.post(
        "/action/finish",
        data={"_step": "2", "target": "edge-01", "token": "s3cr3t"},
        headers={"HX-Request": "true"},
    )
    assert len(AUDIT_EVENTS) == 1
    event = AUDIT_EVENTS[0]
    assert event["action"] == "finish"
    assert event["model_name"] is None  # no model on this wizard
    assert event["data"]["target"] == "edge-01"
    assert event["data"]["token"] == MASK


def test_crud_create_still_audits_through_the_shared_mixin(audit_client):
    """CRUDView and WizardView emit through the same _AuditMixin — guard the move."""
    MemberView.audit_log = True
    try:
        audit_client.post(
            "/member/create",
            data={"username": "grace", "email": "grace@example.com"},
            headers={"HX-Request": "true"},
        )
    finally:
        MemberView.audit_log = False
    assert len(AUDIT_EVENTS) == 1
    event = AUDIT_EVENTS[0]
    assert event["action"] == "create"
    assert event["model_name"] == "Member"
    assert event["view_name"] == "member"
    assert event["data"]["username"] == "grace"


def test_audit_log_defaults_to_off(audit_client):
    audit_client.post(
        "/quiet/finish",
        data={"_step": "1", "username": "ada", "email": "ada@example.com"},
        headers={"HX-Request": "true"},
    )
    assert AUDIT_EVENTS == []


# ---------------------------------------------------------------------------
# Unexpected errors must not cost the user the wizard
# ---------------------------------------------------------------------------


class ExplodingWizard(WizardView):
    """Hooks that raise something other than ValidationError."""

    name = "boom"
    model = Member
    steps = [
        {"label": "One", "fields": ["username", "email"]},
        {"label": "Two", "fields": ["nickname"]},
    ]

    def on_step(self, step, data, db, request=None):
        if data.get("username") == "trip-step":
            raise RuntimeError("step blew up")

    def on_finish(self, data, db, request=None):
        raise RuntimeError("finish blew up")


@pytest.fixture()
def boom_client():
    engine = init_db(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = FastAPI()
    admin = Admin(app)
    admin.add_view(MemberView)
    admin.add_view(ExplodingWizard)
    yield TestClient(app)
    Base.metadata.drop_all(engine)


def test_unexpected_error_in_on_step_re_renders_instead_of_500(boom_client):
    resp = boom_client.post(
        "/boom/step/2",
        data={"_step": "1", "username": "trip-step", "email": "a@b.com"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert _toast(resp) == "step blew up"
    # Back on the step they submitted, with what they typed still there.
    assert 'id="wizard-step-1"' in resp.text
    assert 'value="trip-step"' in resp.text


def test_unexpected_error_in_on_finish_re_renders_instead_of_500(boom_client):
    resp = boom_client.post(
        "/boom/finish",
        data={"_step": "2", "username": "ada", "email": "a@b.com", "nickname": "Countess"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert _toast(resp) == "finish blew up"
    assert 'id="wizard-step-2"' in resp.text
    assert 'value="Countess"' in resp.text


# ---------------------------------------------------------------------------
# before_save — the wizard's on_model_change
# ---------------------------------------------------------------------------


class DerivingWizard(WizardView):
    """Fills in a field the user never sees, and can veto the save."""

    name = "derive"
    model = Member
    steps = [{"label": "Account", "fields": ["username", "email"]}]

    def before_save(self, item, data, db, request=None):
        if item.username == "banned":
            raise ValidationError("That username is not allowed")
        item.nickname = item.username.title()


@pytest.fixture()
def derive_client():
    engine = init_db(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = FastAPI()
    admin = Admin(app)
    admin.add_view(MemberView)
    admin.add_view(DerivingWizard)
    yield TestClient(app)
    Base.metadata.drop_all(engine)


def test_before_save_can_mutate_the_item(derive_client):
    derive_client.post(
        "/derive/finish",
        data={"_step": "1", "username": "ada", "email": "ada@example.com"},
        headers={"HX-Request": "true"},
    )
    db = next(get_db())
    try:
        # Set by before_save, never submitted by the user.
        assert db.query(Member).one().nickname == "Ada"
    finally:
        db.close()


def test_before_save_can_veto_the_save(derive_client):
    resp = derive_client.post(
        "/derive/finish",
        data={"_step": "1", "username": "banned", "email": "b@example.com"},
        headers={"HX-Request": "true"},
    )
    assert _toast(resp) == "That username is not allowed"
    db = next(get_db())
    try:
        assert db.query(Member).count() == 0  # nothing committed
    finally:
        db.close()
