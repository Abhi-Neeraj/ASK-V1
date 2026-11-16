"""Bluetooth lockets: registering, people per locket, and pairing with an emailed code."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeutil
from app.models import AuditEvent
from app.security.locket_codes import bond_proof, locket_secret, pairing_code
from app.services.email import MemoryEmailSender
from tests.conftest import new_web
from tests.helpers import WebClient

MASTER = "test-master-key-0123456789abcdef0123456789"
NONCE = "00112233445566778899aabbccddeeff"
OTHER_NONCE = "ffeeddccbbaa99887766554433221100"


def as_user(app, email: str) -> WebClient:  # type: ignore[no-untyped-def]
    client = new_web(app)
    assert client.login(email).status_code == 200
    return client


def register(admin: WebClient, name: str = "Meeting locket") -> dict[str, str]:
    created = admin.post("/api/lockets", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()  # type: ignore[no-any-return]


def code_from(outbox: MemoryEmailSender) -> str:
    match = re.search(r"\b(\d{6})\b", outbox.outbox[-1].body)
    assert match is not None
    return match.group(1)


def proof(product_id: str, nonce: str = NONCE) -> str:
    """What the real locket shows the phone once Bluetooth pairing succeeded."""
    return bond_proof(locket_secret(MASTER, product_id), bytes.fromhex(nonce))


def confirm(client: WebClient, product_id: str, nonce: str = NONCE):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/lockets/{product_id}/paired", json={"nonce": nonce, "proof": proof(product_id, nonce)}
    )


def pair(client: WebClient, product_id: str, nonce: str = NONCE) -> int:
    asked = client.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": nonce})
    assert asked.status_code == 200, asked.text
    return confirm(client, product_id, nonce).status_code  # type: ignore[no-any-return]


@pytest.fixture
def admin(app, acme) -> WebClient:  # type: ignore[no-untyped-def]
    return as_user(app, "admin@acme.example.com")


@pytest.fixture
def employee(app, acme) -> WebClient:  # type: ignore[no-untyped-def]
    return as_user(app, "employee@acme.example.com")


@pytest.fixture
def manager(app, acme) -> WebClient:  # type: ignore[no-untyped-def]
    return as_user(app, "manager@acme.example.com")


def test_admin_registers_a_locket_and_gets_the_secret_once(admin: WebClient) -> None:
    created = register(admin)
    assert created["productId"].startswith("ASK-")
    assert created["secretHex"] == locket_secret(MASTER, created["productId"]).hex()
    listing = admin.get("/api/lockets")
    assert listing.status_code == 200
    assert "secretHex" not in listing.text and created["secretHex"] not in listing.text
    [locket] = listing.json()
    assert locket["productId"] == created["productId"] and locket["maxUsers"] == 1 and locket["members"] == []


def test_only_admin_registers(manager: WebClient) -> None:
    assert manager.post("/api/lockets", json={"name": "x"}).status_code == 403


def test_registering_without_a_master_key_is_refused(app, admin: WebClient) -> None:  # type: ignore[no-untyped-def]
    app.state.settings.locket_master_key = None
    refused = admin.post("/api/lockets", json={"name": "x"})
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "locket_keys_missing"


def test_code_is_emailed_and_matches_the_formula(
    admin: WebClient, employee: WebClient, outbox: MemoryEmailSender
) -> None:
    product_id = register(admin)["productId"]
    asked = employee.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE})
    assert asked.status_code == 200
    body = asked.json()
    assert body["sentTo"] == "e***@acme.example.com" and "expiresAt" in body
    message = outbox.outbox[-1]
    assert message.to == "employee@acme.example.com"
    expected = pairing_code(locket_secret(MASTER, product_id), bytes.fromhex(NONCE))
    assert code_from(outbox) == expected
    assert expected not in asked.text


def test_bad_nonce_is_refused(admin: WebClient, employee: WebClient) -> None:
    product_id = register(admin)["productId"]
    assert employee.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": "12"}).status_code == 422


def test_paired_adds_the_member_once_and_completes_the_request(admin: WebClient, employee: WebClient) -> None:
    product_id = register(admin)["productId"]
    assert pair(employee, product_id) == 204
    assert confirm(employee, product_id).status_code == 404
    [locket] = employee.get("/api/lockets").json()
    assert [m["email"] for m in locket["members"]] == ["employee@acme.example.com"]


def test_full_locket_refuses_a_code_and_sends_no_email(
    admin: WebClient, employee: WebClient, manager: WebClient, outbox: MemoryEmailSender
) -> None:
    product_id = register(admin)["productId"]
    assert pair(employee, product_id) == 204
    sent = len(outbox.outbox)
    refused = manager.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": OTHER_NONCE})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "locket_full"
    assert len(outbox.outbox) == sent


def test_existing_member_can_get_a_new_code_when_full(admin: WebClient, employee: WebClient) -> None:
    product_id = register(admin)["productId"]
    assert pair(employee, product_id) == 204
    assert pair(employee, product_id, OTHER_NONCE) == 204  # a new phone, same person


def test_paired_respects_the_limit_even_after_the_code_was_sent(
    admin: WebClient, employee: WebClient, manager: WebClient
) -> None:
    product_id = register(admin)["productId"]
    for client in (employee, manager):
        asked = client.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE})
        assert asked.status_code == 200
    assert confirm(employee, product_id).status_code == 204
    late = confirm(manager, product_id)
    assert late.status_code == 409 and late.json()["error"]["code"] == "locket_full"


def test_expired_request_cannot_be_confirmed(
    admin: WebClient, employee: WebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    product_id = register(admin)["productId"]
    assert employee.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code == 200
    later = timeutil.utcnow() + timedelta(minutes=11)
    monkeypatch.setattr(timeutil, "utcnow", lambda: later)
    gone = confirm(employee, product_id)
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "pairing_request_not_found"


def test_code_requests_are_rate_limited(admin: WebClient, employee: WebClient) -> None:
    product_id = register(admin)["productId"]
    for _ in range(5):
        assert (
            employee.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code == 200
        )
    assert employee.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code == 429


def test_only_admin_changes_the_limit(admin: WebClient, manager: WebClient) -> None:
    product_id = register(admin)["productId"]
    assert manager.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": 2}).status_code == 403
    raised = admin.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": 2})
    assert raised.status_code == 200 and raised.json()["maxUsers"] == 2


def test_a_raised_limit_lets_a_second_person_on(
    admin: WebClient, employee: WebClient, manager: WebClient
) -> None:
    product_id = register(admin)["productId"]
    assert pair(employee, product_id) == 204
    admin.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": 2})
    assert pair(manager, product_id, OTHER_NONCE) == 204


def test_limit_cannot_drop_below_members(admin: WebClient, employee: WebClient, manager: WebClient) -> None:
    product_id = register(admin)["productId"]
    admin.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": 2})
    assert pair(employee, product_id) == 204
    assert pair(manager, product_id, OTHER_NONCE) == 204
    refused = admin.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": 1})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "limit_below_members"


@pytest.mark.parametrize("value", [0, 51])
def test_limit_bounds(admin: WebClient, value: int) -> None:
    product_id = register(admin)["productId"]
    assert admin.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": value}).status_code == 422


def test_member_can_forget_a_locket(admin: WebClient, employee: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
    product_id = register(admin)["productId"]
    assert pair(employee, product_id) == 204
    me = acme["employee"].id
    assert employee.delete(f"/api/lockets/{product_id}/members/{me}").status_code == 204
    assert employee.get("/api/lockets").json() == []


def test_employee_cannot_remove_someone_else(  # type: ignore[no-untyped-def]
    admin: WebClient, employee: WebClient, manager: WebClient, acme
) -> None:
    product_id = register(admin)["productId"]
    admin.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": 2})
    assert pair(employee, product_id) == 204
    assert pair(manager, product_id, OTHER_NONCE) == 204
    other = acme["manager"].id
    assert employee.delete(f"/api/lockets/{product_id}/members/{other}").status_code == 403
    assert manager.delete(f"/api/lockets/{product_id}/members/{acme['employee'].id}").status_code == 204


def test_employees_only_see_lockets_they_are_on(
    admin: WebClient, employee: WebClient, manager: WebClient
) -> None:
    mine = register(admin, "Mine")["productId"]
    register(admin, "Not mine")
    assert pair(employee, mine) == 204
    assert [locket["productId"] for locket in employee.get("/api/lockets").json()] == [mine]
    assert len(manager.get("/api/lockets").json()) == 2


def test_other_company_locket_is_404(app, admin: WebClient, globex) -> None:  # type: ignore[no-untyped-def]
    product_id = register(admin)["productId"]
    outsider = as_user(app, "employee@globex.example.com")
    assert outsider.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code == 404
    assert confirm(outsider, product_id).status_code == 404


def test_emails_never_contain_the_secret_and_actions_are_audited(
    admin: WebClient, employee: WebClient, outbox: MemoryEmailSender, db: Session
) -> None:
    created = register(admin)
    product_id = created["productId"]
    assert pair(employee, product_id) == 204
    admin.patch(f"/api/lockets/{product_id}/limit", json={"maxUsers": 3})
    assert all(created["secretHex"] not in m.body for m in outbox.outbox)
    actions = set(db.scalars(select(AuditEvent.action)))
    assert {"locket.registered", "locket.code_requested", "locket.paired", "locket.limit_changed"} <= actions
    details = " ".join(str(d) for d in db.scalars(select(AuditEvent.detail)))
    assert created["secretHex"] not in details and code_from(outbox) not in details


def test_assigning_through_devices_patch_makes_that_person_the_one_member(  # type: ignore[no-untyped-def]
    admin: WebClient, employee: WebClient, acme
) -> None:
    product_id = register(admin)["productId"]
    device = next(d for d in admin.get("/api/devices").json() if d["deviceId"] == product_id)
    assigned = admin.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(acme["employee"].id)})
    assert assigned.status_code == 200
    [locket] = employee.get("/api/lockets").json()
    assert [m["email"] for m in locket["members"]] == ["employee@acme.example.com"]


def test_paired_needs_the_lockets_bonding_proof(admin: WebClient, employee: WebClient) -> None:
    """Asking for a code and confirming it is not enough: without the locket's proof nobody gets on."""
    product_id = register(admin)["productId"]
    assert employee.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code == 200
    missing = employee.post(f"/api/lockets/{product_id}/paired", json={"nonce": NONCE})
    assert missing.status_code == 422
    forged = employee.post(f"/api/lockets/{product_id}/paired", json={"nonce": NONCE, "proof": "0" * 64})
    assert forged.status_code == 403 and forged.json()["error"]["code"] == "pairing_proof_invalid"
    other = employee.post(
        f"/api/lockets/{product_id}/paired", json={"nonce": NONCE, "proof": proof(product_id, OTHER_NONCE)}
    )
    assert other.status_code == 403
    assert employee.get("/api/lockets").json() == []
    assert confirm(employee, product_id).status_code == 204  # the real proof still works


def test_wifi_devices_cannot_be_joined_by_code(admin: WebClient, employee: WebClient) -> None:
    wifi = admin.post("/api/devices", json={"name": "Wi-Fi locket"})
    assert wifi.status_code == 201
    product_id = wifi.json()["deviceId"]
    assert employee.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code == 404
    assert [locket["productId"] for locket in admin.get("/api/lockets").json()] == []


def test_code_requests_are_also_limited_per_locket(
    admin: WebClient, employee: WebClient, manager: WebClient, app
) -> None:  # type: ignore[no-untyped-def]
    app.state.settings.locket_code_requests_per_locket_per_hour = 6
    product_id = register(admin)["productId"]
    for client in (employee, manager):
        for _ in range(3):
            assert (
                client.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code
                == 200
            )
    assert admin.post(f"/api/lockets/{product_id}/pairing-code", json={"nonce": NONCE}).status_code == 429


def test_probing_unknown_lockets_counts_toward_the_limit(employee: WebClient) -> None:
    for _ in range(5):
        assert employee.post("/api/lockets/ASK-NOPE00/pairing-code", json={"nonce": NONCE}).status_code == 404
    assert employee.post("/api/lockets/ASK-NOPE00/pairing-code", json={"nonce": NONCE}).status_code == 429
