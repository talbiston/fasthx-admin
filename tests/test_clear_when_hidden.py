"""Tests for clearing fields that ``depends_on`` hid from the user.

Hiding is CSS-only, so a hidden input still posts — on an edit that means the
value already on the record, which would be written straight back for a record
it no longer applies to. These cover the save-time clearing that prevents it,
and the ``clear_when_hidden: False`` opt-out for values that must survive.
"""

import os

# Must be set before importing fasthx_admin.auth (it reads the env at import).
os.environ["AUTH_DISABLED"] = "1"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.pool import StaticPool

from fasthx_admin import Admin, CRUDView
from fasthx_admin.database import Base, get_db, init_db


class Device(Base):
    __tablename__ = "device"
    id = Column(Integer, primary_key=True)
    is_pppoe = Column(Boolean, default=False)
    override_hostname = Column(Boolean, default=False)
    # Hidden while PPPoE is on — the case that motivated this.
    address = Column(String, nullable=True)
    # Hidden while PPPoE is off.
    pppoe_id = Column(String, nullable=True)
    # Hidden unless overridden, but regenerated downstream: must not be cleared.
    hostname = Column(String, nullable=True)
    # Hidden with PPPoE, but has no empty value it would accept.
    region = Column(String, nullable=False, default="")
    # Hidden with PPPoE; clears to False rather than None.
    monitored = Column(Boolean, default=False)


class DeviceView(CRUDView):
    model = Device
    name = "device"
    form_columns = [
        "is_pppoe",
        "override_hostname",
        "address",
        "pppoe_id",
        "hostname",
        "region",
        "monitored",
    ]
    form_widget_overrides = {
        "address": {"depends_on": ["!is_pppoe"]},
        "region": {"depends_on": ["!is_pppoe"]},
        "monitored": {"depends_on": ["!is_pppoe"]},
        "pppoe_id": {"depends_on": "is_pppoe"},
        "hostname": {"depends_on": "override_hostname", "clear_when_hidden": False},
    }


@pytest.fixture()
def client():
    engine = init_db(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # single shared in-memory connection
    )
    Base.metadata.create_all(engine)

    app = FastAPI()
    admin = Admin(app)
    admin.add_view(DeviceView)
    yield TestClient(app)

    Base.metadata.drop_all(engine)


def _seed(**kwargs):
    db = next(get_db())
    device = Device(id=1, **kwargs)
    db.add(device)
    db.commit()
    db.close()


def _reload():
    db = next(get_db())
    try:
        return db.query(Device).get(1)
    finally:
        db.close()


def test_hidden_field_is_cleared_instead_of_posting_its_stale_value(client):
    _seed(address="10.0.0.1/24", region="", monitored=False)
    # The browser posts the address it is still holding behind the curtain.
    client.post(
        "/device/1/edit",
        data={"is_pppoe": "true", "address": "10.0.0.1/24", "pppoe_id": "ss-42"},
    )
    device = _reload()
    assert device.address is None, "hidden address should not be written back"
    assert device.pppoe_id == "ss-42", "visible field saves normally"


def test_visible_field_saves_the_posted_value(client):
    _seed(address="10.0.0.1/24")
    client.post("/device/1/edit", data={"address": "192.168.1.1/24"})
    assert _reload().address == "192.168.1.1/24"


def test_field_hidden_by_an_unchecked_box_is_cleared(client):
    # pppoe_id shows only while is_pppoe is on; turning it off clears the id.
    _seed(is_pppoe=True, pppoe_id="ss-42")
    client.post("/device/1/edit", data={"address": "10.0.0.1/24", "pppoe_id": "ss-42"})
    device = _reload()
    assert device.pppoe_id is None
    assert device.address == "10.0.0.1/24"


def test_clear_when_hidden_false_keeps_the_stored_value(client):
    # hostname is hidden (override unticked) but regenerated downstream.
    _seed(hostname="FMG-TX-001", override_hostname=False)
    client.post("/device/1/edit", data={"hostname": "FMG-TX-001"})
    assert _reload().hostname == "FMG-TX-001"


def test_not_null_column_is_left_alone_rather_than_failing_the_save(client):
    _seed(address="10.0.0.1/24", region="TX")
    resp = client.post(
        "/device/1/edit", data={"is_pppoe": "true", "region": "TX"}
    )
    assert resp.status_code < 400
    device = _reload()
    assert device.region == "TX", "no empty value a NOT NULL column would accept"
    assert device.address is None, "nullable sibling still clears"


def test_hidden_boolean_clears_to_false_not_null(client):
    _seed(monitored=True, address="10.0.0.1/24")
    client.post("/device/1/edit", data={"is_pppoe": "true", "monitored": "true"})
    assert _reload().monitored is False


def test_create_is_unaffected_when_nothing_is_hidden(client):
    resp = client.post(
        "/device/create", data={"address": "10.0.0.1/24", "region": "TX"}
    )
    assert resp.status_code < 400
    db = next(get_db())
    device = db.query(Device).order_by(Device.id.desc()).first()
    db.close()
    assert device.address == "10.0.0.1/24"
