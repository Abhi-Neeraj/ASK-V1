from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeutil
from app.main import create_app
from app.models import AuditEvent, DeviceCredential, DeviceEvent, LiveEvent
from app.services.housekeeping import run_housekeeping
from tests.conftest import new_web, provision_device
from tests.helpers import DeviceClient, make_wav


def manager_web(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def employee_web(app, email: str = "employee@acme.example.com"):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login(email)
    return web


class TestCreationAndPairing:
    def test_a_manager_creates_a_device_and_gets_a_one_time_code(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        created = web.post("/api/devices", json={"name": "  Front desk  "})
        assert created.status_code == 201
        body = created.json()
        assert re.fullmatch(r"[A-Z2-9]{5}-[A-Z2-9]{5}", body["code"])
        assert re.fullmatch(r"ASK-[A-Z2-9]{6}", body["deviceId"])
        assert body["deviceName"] == "Front desk"

        listed = web.get("/api/devices").json()
        assert len(listed) == 1
        assert listed[0]["status"] == "pending"
        assert listed[0]["batteryPercent"] is None
        assert listed[0]["firmwareVersion"] is None
        assert listed[0]["lastHeartbeatAt"] is None

    def test_employees_cannot_create_devices(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        response = employee_web(app).post("/api/devices", json={"name": "Sneaky"})
        assert response.status_code == 403

    def test_pairing_returns_a_credential_once_and_the_device_comes_online_on_heartbeat(
        self, app, acme
    ) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        code = web.post("/api/devices", json={"name": "Locket One"}).json()["code"]
        locket = DeviceClient(TestClient(app))
        paired = locket.pair(code)
        assert paired.status_code == 200
        body = paired.json()
        assert re.fullmatch(r"ASK-[A-Z2-9]{6}\.[A-Za-z0-9_-]{40,}", body["credential"])
        assert body["config"]["chunkSeconds"] == 10
        assert body["config"]["sampleRates"] == [16000]
        # The limits a locket must stay inside, to split a long recording into clips the server accepts.
        assert body["config"]["maxClipSeconds"] == 600
        assert body["config"]["maxUploadBytes"] == 25 * 1024 * 1024
        locket.credential = body["credential"]

        # Paired is not online: presence comes from real heartbeats only.
        assert web.get("/api/devices").json()[0]["status"] == "offline"
        assert locket.heartbeat(firmwareVersion="0.9.2").status_code == 200
        device = web.get("/api/devices").json()[0]
        assert device["status"] == "online"
        assert device["firmwareVersion"] == "0.9.2"

    def test_the_credential_is_stored_only_as_a_hash(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        _, locket = provision_device(app, manager_web(app))
        assert locket.credential is not None
        secret = locket.credential.split(".", 1)[1]
        stored = db.scalar(select(DeviceCredential.secret_hash))
        assert stored is not None and secret not in stored and stored != secret

    def test_a_code_works_exactly_once(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        code = web.post("/api/devices", json={"name": "Locket One"}).json()["code"]
        assert DeviceClient(TestClient(app)).pair(code).status_code == 200
        again = DeviceClient(TestClient(app)).pair(code)
        assert again.status_code == 400
        assert again.json()["error"]["code"] == "invalid_pairing_code"

    def test_codes_expire(self, app, acme, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
        code = manager_web(app).post("/api/devices", json={"name": "Locket One"}).json()["code"]
        later = timeutil.utcnow() + timedelta(minutes=11)
        monkeypatch.setattr(timeutil, "utcnow", lambda: later)
        assert DeviceClient(TestClient(app)).pair(code).status_code == 400

    def test_codes_are_forgiving_about_case_and_dashes_but_not_wrong_digits(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        code = manager_web(app).post("/api/devices", json={"name": "Locket One"}).json()["code"]
        wrong = code[:-1] + ("2" if code[-1] != "2" else "3")
        assert DeviceClient(TestClient(app)).pair(wrong).status_code == 400
        sloppy = code.lower().replace("-", " ")
        assert DeviceClient(TestClient(app)).pair(sloppy).status_code == 200

    def test_guessing_codes_is_rate_limited(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        locket = DeviceClient(TestClient(app))
        codes = [locket.pair("AAAAA-AAAAA").status_code for _ in range(12)]
        assert codes[:10] == [400] * 10
        assert 429 in codes[10:]

    def test_regenerating_a_code_retires_the_old_one(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        created = web.post("/api/devices", json={"name": "Locket One"}).json()
        fresh = web.post(f"/api/devices/{created['id']}/pairing").json()
        assert fresh["code"] != created["code"]
        assert DeviceClient(TestClient(app)).pair(created["code"]).status_code == 400
        assert DeviceClient(TestClient(app)).pair(fresh["code"]).status_code == 200
        # Once paired there is nothing to regenerate.
        assert web.post(f"/api/devices/{created['id']}/pairing").status_code == 409

    def test_a_revoked_locket_cannot_be_paired_again_and_the_answer_says_so(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        created = web.post("/api/devices", json={"name": "Locket One"}).json()
        assert web.post(f"/api/devices/{created['id']}/revoke").status_code == 200

        response = web.post(f"/api/devices/{created['id']}/pairing")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "device_revoked"
        # A code that was outstanding when the locket was revoked is dead as well.
        assert DeviceClient(TestClient(app)).pair(created["code"]).status_code == 400

    def test_pairing_is_audited_and_recorded_in_the_device_history(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        device, _ = provision_device(app, web)
        history = {e["type"] for e in web.get(f"/api/devices/{device['id']}/events").json()}
        assert {"created", "paired"} <= history
        actions = set(db.scalars(select(AuditEvent.action)))
        assert {"device.created", "device.paired"} <= actions


@pytest.fixture
def strict_app(settings, engine, outbox):  # type: ignore[no-untyped-def]
    """A server that only accepts real lockets, like production does by default."""
    refusing = settings.model_copy(update={"allow_simulated_devices": False})
    return create_app(refusing, engine=engine, email=outbox)


class TestSimulatedLockets:
    def pair_as(self, app, web, *, simulated: bool | None):  # type: ignore[no-untyped-def]
        created = web.post("/api/devices", json={"name": "Stand-in"}).json()
        locket = DeviceClient(TestClient(app))
        response = locket.pair(created["code"], simulated=simulated)
        return created, locket, response

    def test_a_simulated_locket_is_marked_everywhere_it_appears(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        created, locket, response = self.pair_as(app, web, simulated=True)
        assert response.status_code == 200
        locket.credential = response.json()["credential"]
        assert locket.heartbeat().status_code == 200

        device = web.get(f"/api/devices/{created['id']}").json()
        assert device["simulated"] is True

        uploaded = locket.upload("clip-sim-00001", make_wav(1.0))
        assert uploaded.status_code == 201
        recording = web.get(f"/api/recordings/{uploaded.json()['recordingId']}").json()
        assert recording["simulated"] is True
        listed = web.get("/api/recordings").json()["items"]
        assert [r["simulated"] for r in listed] == [True]

    def test_real_lockets_are_never_marked(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        for number, simulated in enumerate((None, False)):
            created, locket, response = self.pair_as(app, web, simulated=simulated)
            assert response.status_code == 200
            locket.credential = response.json()["credential"]
            assert web.get(f"/api/devices/{created['id']}").json()["simulated"] is False
            assert locket.upload(f"clip-real-0000{number}", make_wav(1.0)).status_code == 201

        items = web.get("/api/recordings").json()["items"]
        assert len(items) == 2
        assert [r["simulated"] for r in items] == [False, False]

    def test_a_server_that_only_accepts_real_lockets_refuses_a_simulator(self, strict_app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(strict_app)
        created, _, response = self.pair_as(strict_app, web, simulated=True)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "simulation_not_allowed"
        assert web.get(f"/api/devices/{created['id']}").json()["status"] == "pending"

    def test_a_refused_simulator_does_not_use_up_the_pairing_code(self, strict_app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(strict_app)
        created, _, refused = self.pair_as(strict_app, web, simulated=True)
        assert refused.status_code == 422
        # The same code still works for the real locket it was made for.
        real = DeviceClient(TestClient(strict_app)).pair(created["code"])
        assert real.status_code == 200
        assert web.get(f"/api/devices/{created['id']}").json()["simulated"] is False

    def test_a_server_that_only_accepts_real_lockets_still_pairs_real_ones(self, strict_app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(strict_app)
        _, _, response = self.pair_as(strict_app, web, simulated=None)
        assert response.status_code == 200

    def test_the_choice_is_recorded_in_the_device_history(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        created, _, _ = self.pair_as(app, web, simulated=True)
        paired = next(
            e for e in web.get(f"/api/devices/{created['id']}/events").json() if e["type"] == "paired"
        )
        assert paired["detail"]["simulated"] is True


class TestDeviceAuthentication:
    def test_requests_without_a_valid_credential_are_refused(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        _, locket = provision_device(app, manager_web(app))
        anonymous = DeviceClient(TestClient(app))
        assert anonymous.heartbeat().status_code == 401
        assert (
            locket.request("POST", "/api/device/v1/heartbeat", credential="garbage", json={}).status_code
            == 401
        )
        assert locket.credential is not None
        public_id = locket.credential.split(".")[0]
        assert (
            locket.request(
                "POST", "/api/device/v1/heartbeat", credential=f"{public_id}.wrongsecret", json={}
            ).status_code
            == 401
        )

    def test_one_devices_secret_does_not_work_for_another(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        _, first = provision_device(app, web, "One")
        second_device, second = provision_device(app, web, "Two")
        assert first.credential and second.credential
        crossed = f"{second_device['deviceId']}.{first.credential.split('.', 1)[1]}"
        assert (
            first.request("POST", "/api/device/v1/heartbeat", credential=crossed, json={}).status_code == 401
        )

    def test_a_cookie_session_cannot_be_used_as_a_device(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        assert web.post("/api/device/v1/heartbeat", json={}).status_code == 401

    def test_bad_telemetry_is_rejected_and_unknown_values_stay_unknown(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        _, locket = provision_device(app, web)
        assert locket.heartbeat(batteryPercent=101).status_code == 422
        assert locket.heartbeat(uploadQueueDepth=-1).status_code == 422
        assert locket.heartbeat().status_code == 200
        device = web.get("/api/devices").json()[0]
        assert device["batteryPercent"] is None  # never invented
        assert device["uploadQueueDepth"] is None
        assert locket.heartbeat(batteryPercent=64, uploadQueueDepth=2).status_code == 200
        device = web.get("/api/devices").json()[0]
        assert device["batteryPercent"] == 64
        assert device["uploadQueueDepth"] == 2


class TestPresence:
    def test_a_silent_device_goes_offline_and_comes_back(
        self, app, acme, db: Session, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        _, locket = provision_device(app, web)
        locket.heartbeat()
        assert web.get("/api/devices").json()[0]["status"] == "online"

        start = timeutil.utcnow()
        monkeypatch.setattr(
            timeutil, "utcnow", lambda: start + timedelta(seconds=settings.heartbeat_expiry_seconds + 5)
        )
        assert web.get("/api/devices").json()[0]["status"] == "offline"

        # The sweep announces the change once, however many times it runs.
        assert run_housekeeping(app.state.session_factory, settings)["devices_marked_offline"] == 1
        assert run_housekeeping(app.state.session_factory, settings)["devices_marked_offline"] == 0
        types = [e.type for e in db.scalars(select(LiveEvent).order_by(LiveEvent.id))]
        assert types.count("device.offline") == 1

        # Reconnection after an interruption.
        monkeypatch.setattr(timeutil, "utcnow", lambda: start + timedelta(seconds=300))
        assert locket.heartbeat().status_code == 200
        assert web.get("/api/devices").json()[0]["status"] == "online"
        db.expire_all()
        types = [e.type for e in db.scalars(select(LiveEvent).order_by(LiveEvent.id))]
        assert types.count("device.online") == 2

    def test_the_first_heartbeat_announces_the_device_to_its_company_only(
        self, app, acme, globex, db: Session
    ) -> None:  # type: ignore[no-untyped-def]
        _, locket = provision_device(app, manager_web(app))
        locket.heartbeat()
        online = list(db.scalars(select(LiveEvent).where(LiveEvent.type == "device.online")))
        assert len(online) == 1
        assert online[0].company_id == acme["company"].id
        assert online[0].company_id != globex["company"].id


class TestConnect:
    def test_connect_requires_a_really_online_device(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        device, locket = provision_device(app, web)
        offline = web.post(f"/api/devices/{device['id']}/connect")
        assert offline.status_code == 409
        assert offline.json()["error"]["code"] == "device_offline"

        locket.heartbeat()
        connected = web.post(f"/api/devices/{device['id']}/connect")
        assert connected.status_code == 200
        assert connected.json()["connected"] is True
        assert web.post(f"/api/devices/{device['id']}/connect").status_code == 200  # idempotent
        assert web.get(f"/api/devices/{device['id']}").json()["connected"] is True

    def test_disconnect_only_ends_the_connection(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        device, locket = provision_device(app, web)
        locket.heartbeat()
        web.post(f"/api/devices/{device['id']}/connect")
        after = web.post(f"/api/devices/{device['id']}/disconnect")
        assert after.status_code == 200
        assert after.json()["connected"] is False
        assert after.json()["status"] == "online"  # the locket itself is untouched
        assert web.post(f"/api/devices/{device['id']}/disconnect").status_code == 200  # idempotent

    def test_each_person_has_their_own_connection(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        admin = new_web(app)
        admin.login("admin@acme.example.com")
        manager = manager_web(app)
        device, locket = provision_device(app, manager)
        locket.heartbeat()
        manager.post(f"/api/devices/{device['id']}/connect")
        assert admin.get(f"/api/devices/{device['id']}").json()["connected"] is False


class TestVisibilityByRole:
    def test_employees_see_only_locket_assigned_to_them(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        mine, my_locket = provision_device(app, manager, "Mine")
        other, _ = provision_device(app, manager, "Someone else's")
        my_locket.heartbeat()
        employee = employee_web(app)

        assert employee.get("/api/devices").json() == []
        assert employee.get(f"/api/devices/{mine['id']}").status_code == 404

        manager.patch(f"/api/devices/{mine['id']}", json={"assignedUserId": str(acme["employee"].id)})
        visible = employee.get("/api/devices").json()
        assert [d["name"] for d in visible] == ["Mine"]
        assert visible[0]["assignedTo"]["name"] == acme["employee"].name
        assert employee.post(f"/api/devices/{mine['id']}/connect").status_code == 200
        assert employee.get(f"/api/devices/{other['id']}").status_code == 404
        assert employee.post(f"/api/devices/{other['id']}/connect").status_code == 404

    def test_employees_cannot_manage_devices(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        device, _ = provision_device(app, manager)
        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(acme["employee"].id)})
        employee = employee_web(app)
        assert employee.patch(f"/api/devices/{device['id']}", json={"name": "Renamed"}).status_code == 403
        assert employee.post(f"/api/devices/{device['id']}/revoke").status_code == 403
        assert employee.get(f"/api/devices/{device['id']}/events").status_code == 403

    def test_unassigning_removes_the_previous_users_connection(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        device, locket = provision_device(app, manager)
        locket.heartbeat()
        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(acme["employee"].id)})
        employee = employee_web(app)
        employee.post(f"/api/devices/{device['id']}/connect")
        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": None})
        assert employee.get(f"/api/devices/{device['id']}").status_code == 404
        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(acme["employee"].id)})
        assert employee.get(f"/api/devices/{device['id']}").json()["connected"] is False

    def test_a_device_cannot_be_assigned_to_someone_from_another_company(self, app, acme, globex) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        device, _ = provision_device(app, manager)
        response = manager.patch(
            f"/api/devices/{device['id']}", json={"assignedUserId": str(globex["employee"].id)}
        )
        assert response.status_code == 422


class TestRevocation:
    def test_a_revoked_locket_is_cut_off_immediately(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        device, locket = provision_device(app, manager)
        locket.heartbeat()
        manager.post(f"/api/devices/{device['id']}/connect")

        revoked = manager.post(f"/api/devices/{device['id']}/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        assert locket.heartbeat().status_code == 401
        assert manager.post(f"/api/devices/{device['id']}/connect").status_code == 409

    def test_a_revoked_locket_is_hidden_from_employees_and_cannot_be_repaired_with_its_old_code(
        self, app, acme
    ) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        created = manager.post("/api/devices", json={"name": "Lost"}).json()
        manager.post(f"/api/devices/{created['id']}/revoke")
        assert DeviceClient(TestClient(app)).pair(created["code"]).status_code == 400
        manager.patch(f"/api/devices/{created['id']}", json={"assignedUserId": str(acme["employee"].id)})
        assert employee_web(app).get("/api/devices").json() == []

    def test_revocation_is_audited(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        device, _ = provision_device(app, manager)
        manager.post(f"/api/devices/{device['id']}/revoke")
        assert "device.revoked" in set(db.scalars(select(AuditEvent.action)))
        assert "revoked" in set(db.scalars(select(DeviceEvent.type)))


class TestTenantIsolation:
    def test_another_company_cannot_see_or_touch_a_device(self, app, acme, globex) -> None:  # type: ignore[no-untyped-def]
        device, locket = provision_device(app, manager_web(app))
        locket.heartbeat()
        outsider = new_web(app)
        outsider.login("admin@globex.example.com")

        assert outsider.get("/api/devices").json() == []
        did = device["id"]
        for response in (
            outsider.get(f"/api/devices/{did}"),
            outsider.get(f"/api/devices/{did}/status"),
            outsider.get(f"/api/devices/{did}/events"),
            outsider.patch(f"/api/devices/{did}", json={"name": "Hijacked"}),
            outsider.post(f"/api/devices/{did}/connect"),
            outsider.post(f"/api/devices/{did}/revoke"),
            outsider.post(f"/api/devices/{did}/pairing"),
        ):
            assert response.status_code == 404, response.text

    def test_mutations_without_the_csrf_token_are_refused(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        assert manager.post("/api/devices", json={"name": "No token"}, csrf=False).status_code == 403
