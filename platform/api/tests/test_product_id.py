"""A locket's product ID is fixed for good once it is saved: not by the API, not by application code."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.devices import PermanentProductIdError
from tests.helpers import WebClient


def test_new_device_gets_a_product_id(web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
    web.login("admin@acme.example.com")
    created = web.post("/api/devices", json={"name": "Locket"})
    assert created.status_code == 201
    assert created.json()["deviceId"].startswith("ASK-")


def test_changing_a_saved_product_id_raises(db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    device = Device(company_id=acme["company"].id, public_id="ASK-AAAAAA", name="Locket")
    db.add(device)
    db.commit()
    with pytest.raises(PermanentProductIdError):
        device.public_id = "ASK-BBBBBB"
    db.rollback()
    assert db.scalar(select(Device.public_id).where(Device.id == device.id)) == "ASK-AAAAAA"


def test_setting_the_same_value_again_is_harmless(db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    device = Device(company_id=acme["company"].id, public_id="ASK-CCCCCC", name="Locket")
    db.add(device)
    db.commit()
    device.public_id = "ASK-CCCCCC"
    db.commit()


def test_an_unsaved_device_can_still_be_given_its_id(acme) -> None:  # type: ignore[no-untyped-def]
    device = Device(company_id=acme["company"].id, public_id="ASK-DDDDDD", name="Locket")
    device.public_id = "ASK-EEEEEE"
    assert device.public_id == "ASK-EEEEEE"


def test_patch_cannot_change_product_id(web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
    web.login("admin@acme.example.com")
    created = web.post("/api/devices", json={"name": "Locket"}).json()
    listing = web.get("/api/devices").json()
    device = next(d for d in listing if d["deviceId"] == created["deviceId"])
    web.patch(f"/api/devices/{device['id']}", json={"publicId": "ASK-ZZZZZZ", "deviceId": "ASK-ZZZZZZ"})
    after = web.get(f"/api/devices/{device['id']}").json()
    assert after["deviceId"] == created["deviceId"]
