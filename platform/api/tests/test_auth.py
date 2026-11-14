from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeutil
from app.models import AuditEvent, AuthSession, Invitation, User
from app.services import accounts
from app.services.email import MemoryEmailSender
from tests.conftest import new_web
from tests.helpers import ORIGIN, PASSWORD, WebClient


def error_code(response) -> str:  # type: ignore[no-untyped-def]
    return response.json()["error"]["code"]


def token_from(outbox: MemoryEmailSender) -> str:
    link = next(line for line in outbox.outbox[-1].body.splitlines() if "token=" in line)
    url = link[link.index("http") :].strip()
    return parse_qs(urlparse(url).query)["token"][0]


class TestLogin:
    def test_signs_in_and_sets_the_right_cookies(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        response = web.login("admin@acme.example.com")
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "admin@acme.example.com"
        assert body["role"] == "admin"
        assert body["company"]["name"] == "Acme"
        assert body["emailVerified"] is False

        cookies = {c.split("=", 1)[0]: c for c in response.headers.get_list("set-cookie")}
        assert "HttpOnly" in cookies["ask_session"]
        assert "SameSite=lax" in cookies["ask_session"]
        assert "HttpOnly" not in cookies["ask_csrf"]  # the web app has to be able to read it

    def test_me_returns_the_signed_in_user(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        web.login("employee@acme.example.com")
        me = web.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "employee"

    def test_me_without_a_session_is_unauthenticated(self, web: WebClient) -> None:
        response = web.get("/api/auth/me")
        assert response.status_code == 401
        assert error_code(response) == "unauthenticated"

    def test_wrong_password_and_unknown_email_look_identical(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        wrong = web.login("admin@acme.example.com", "not-the-password-at-all")
        unknown = web.login("nobody@acme.example.com", "not-the-password-at-all")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    def test_deactivated_users_cannot_sign_in(self, web: WebClient, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        acme["employee"].is_active = False
        db.commit()
        assert web.login("employee@acme.example.com").status_code == 401

    def test_email_is_case_insensitive(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        assert web.login("ADMIN@Acme.Example.COM").status_code == 200

    def test_repeated_failures_are_rate_limited(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        codes = [web.login("admin@acme.example.com", "wrong-password-here").status_code for _ in range(6)]
        assert codes[:5] == [401] * 5
        assert codes[5] == 429
        limited = web.login("admin@acme.example.com", PASSWORD)
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1

    def test_a_login_from_another_origin_is_refused(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        response = web.post(
            "/api/auth/login",
            json={"email": "admin@acme.example.com", "password": PASSWORD},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert error_code(response) == "bad_origin"

    def test_malformed_input_returns_field_errors(self, web: WebClient) -> None:
        response = web.post("/api/auth/login", json={"email": "not-an-email", "password": ""})
        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "validation_error"
        assert set(body["details"]) == {"email", "password"}

    def test_login_is_audited(self, web: WebClient, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web.login("manager@acme.example.com")
        actions = set(db.scalars(select(AuditEvent.action)))
        assert "auth.login" in actions

    def test_a_wrong_password_is_audited_in_the_members_company(  # type: ignore[no-untyped-def]
        self, web: WebClient, db: Session, acme, globex
    ) -> None:
        guess = "definitely-not-the-password"
        assert web.login("manager@acme.example.com", guess).status_code == 401

        failures = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_failed")).all()
        assert len(failures) == 1
        assert failures[0].company_id == acme["company"].id  # visible to Acme, never to Globex
        assert failures[0].actor_user_id == acme["manager"].id
        assert guess not in str(failures[0].detail)

    def test_an_unknown_address_leaves_no_audit_row(self, web: WebClient, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        assert web.login("nobody@acme.example.com", "definitely-not-the-password").status_code == 401
        assert db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_failed")).all() == []


class TestSessions:
    def test_logout_ends_the_session_on_the_server(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        stolen = dict(web.client.cookies)  # what an attacker who copied the cookie would hold
        assert web.post("/api/auth/logout").status_code == 204

        replay = new_web(web.client.app)
        replay.client.cookies.update(stolen)
        assert replay.get("/api/auth/me").status_code == 401

    def test_logout_without_a_session_is_harmless(self, web: WebClient) -> None:
        assert web.post("/api/auth/logout").status_code == 204

    def test_sessions_expire(self, web: WebClient, acme, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        later = timeutil.utcnow() + timedelta(days=8)
        monkeypatch.setattr(timeutil, "utcnow", lambda: later)
        assert web.get("/api/auth/me").status_code == 401

    def test_idle_sessions_end(self, web: WebClient, acme, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        later = timeutil.utcnow() + timedelta(hours=25)
        monkeypatch.setattr(timeutil, "utcnow", lambda: later)
        assert web.get("/api/auth/me").status_code == 401

    def test_only_a_hash_of_the_cookie_is_stored(self, web: WebClient, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        raw = web.client.cookies["ask_session"]
        stored = db.scalar(select(AuthSession.token_hash))
        assert stored is not None and raw != stored and raw not in stored

    def test_you_can_list_and_revoke_only_your_own_sessions(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        first = new_web(app)
        first.login("admin@acme.example.com")
        second = new_web(app)
        second.login("admin@acme.example.com")
        listing = second.get("/api/auth/sessions").json()
        assert len(listing) == 2
        assert sum(1 for s in listing if s["current"]) == 1

        other = next(s for s in listing if not s["current"])
        assert second.delete(f"/api/auth/sessions/{other['id']}").status_code == 204
        assert first.get("/api/auth/me").status_code == 401

        outsider = new_web(app)
        outsider.login("manager@acme.example.com")
        mine = outsider.get("/api/auth/sessions").json()[0]["id"]
        assert second.delete(f"/api/auth/sessions/{mine}").status_code == 404


class TestCsrf:
    def test_state_changing_requests_need_the_token(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        body = {"currentPassword": PASSWORD, "newPassword": "a-brand-new-passphrase"}
        missing = web.post("/api/auth/change-password", json=body, csrf=False)
        assert missing.status_code == 403
        assert error_code(missing) == "csrf_failed"

        wrong = web.post(
            "/api/auth/change-password", json=body, csrf=False, headers={"x-csrf-token": "guess"}
        )
        assert wrong.status_code == 403

    def test_a_token_from_another_session_does_not_work(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        one = new_web(app)
        one.login("admin@acme.example.com")
        two = new_web(app)
        two.login("manager@acme.example.com")
        body = {"currentPassword": PASSWORD, "newPassword": "a-brand-new-passphrase"}
        borrowed = {"x-csrf-token": one.client.cookies["ask_csrf"]}
        response = two.post("/api/auth/change-password", json=body, csrf=False, headers=borrowed)
        assert response.status_code == 403

    def test_reads_do_not_need_the_token(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        assert web.get("/api/auth/me", csrf=False).status_code == 200

    def test_a_write_from_a_foreign_origin_is_refused_even_with_a_valid_token(
        self, web: WebClient, acme
    ) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        response = web.post(
            "/api/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": "a-brand-new-passphrase"},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert error_code(response) == "bad_origin"


class TestPasswordChange:
    def test_changes_the_password_and_signs_out_other_sessions(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        keep = new_web(app)
        keep.login("admin@acme.example.com")
        other = new_web(app)
        other.login("admin@acme.example.com")

        response = keep.post(
            "/api/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": "a-brand-new-passphrase"},
        )
        assert response.status_code == 204
        assert keep.get("/api/auth/me").status_code == 200
        assert other.get("/api/auth/me").status_code == 401
        assert new_web(app).login("admin@acme.example.com", PASSWORD).status_code == 401
        assert new_web(app).login("admin@acme.example.com", "a-brand-new-passphrase").status_code == 200

    def test_requires_the_current_password_and_a_strong_new_one(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        web.login("admin@acme.example.com")
        wrong = web.post(
            "/api/auth/change-password",
            json={"currentPassword": "nope-nope-nope", "newPassword": "a-brand-new-passphrase"},
        )
        assert wrong.status_code == 422
        weak = web.post(
            "/api/auth/change-password", json={"currentPassword": PASSWORD, "newPassword": "short"}
        )
        assert weak.status_code == 422
        assert "password" in weak.json()["error"]["details"]


class TestPasswordReset:
    def test_full_reset_flow(self, app, acme, outbox: MemoryEmailSender) -> None:  # type: ignore[no-untyped-def]
        signed_in = new_web(app)
        signed_in.login("employee@acme.example.com")

        browser = new_web(app)
        assert (
            browser.post(
                "/api/auth/password-reset/request", json={"email": "employee@acme.example.com"}
            ).status_code
            == 204
        )
        assert len(outbox.outbox) == 1
        assert outbox.outbox[0].to == "employee@acme.example.com"
        token = token_from(outbox)

        confirm = browser.post(
            "/api/auth/password-reset/confirm", json={"token": token, "password": "freshly-chosen-passphrase"}
        )
        assert confirm.status_code == 204
        # Anyone holding the old password loses their session.
        assert signed_in.get("/api/auth/me").status_code == 401
        assert new_web(app).login("employee@acme.example.com", PASSWORD).status_code == 401
        assert new_web(app).login("employee@acme.example.com", "freshly-chosen-passphrase").status_code == 200

        reuse = browser.post(
            "/api/auth/password-reset/confirm", json={"token": token, "password": "another-new-passphrase"}
        )
        assert reuse.status_code == 400
        assert error_code(reuse) == "invalid_token"

    def test_unknown_addresses_get_the_same_answer_and_no_email(
        self, web: WebClient, acme, outbox: MemoryEmailSender
    ) -> None:  # type: ignore[no-untyped-def]
        response = web.post("/api/auth/password-reset/request", json={"email": "ghost@nowhere.example.com"})
        assert response.status_code == 204
        assert outbox.outbox == []

    def test_a_weak_new_password_does_not_use_up_the_link(
        self, web: WebClient, acme, outbox: MemoryEmailSender
    ) -> None:  # type: ignore[no-untyped-def]
        web.post("/api/auth/password-reset/request", json={"email": "employee@acme.example.com"})
        token = token_from(outbox)
        weak = web.post("/api/auth/password-reset/confirm", json={"token": token, "password": "short"})
        assert weak.status_code == 422
        good = web.post(
            "/api/auth/password-reset/confirm", json={"token": token, "password": "freshly-chosen-passphrase"}
        )
        assert good.status_code == 204

    def test_a_newer_link_replaces_the_older_one(
        self, web: WebClient, acme, outbox: MemoryEmailSender
    ) -> None:  # type: ignore[no-untyped-def]
        web.post("/api/auth/password-reset/request", json={"email": "employee@acme.example.com"})
        first = token_from(outbox)
        web.post("/api/auth/password-reset/request", json={"email": "employee@acme.example.com"})
        second = token_from(outbox)
        old = web.post(
            "/api/auth/password-reset/confirm", json={"token": first, "password": "freshly-chosen-passphrase"}
        )
        assert old.status_code == 400
        assert (
            web.post(
                "/api/auth/password-reset/confirm",
                json={"token": second, "password": "freshly-chosen-passphrase"},
            ).status_code
            == 204
        )

    def test_expired_links_are_refused(
        self, web: WebClient, acme, outbox: MemoryEmailSender, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        web.post("/api/auth/password-reset/request", json={"email": "employee@acme.example.com"})
        token = token_from(outbox)
        later = timeutil.utcnow() + timedelta(hours=2)
        monkeypatch.setattr(timeutil, "utcnow", lambda: later)
        response = web.post(
            "/api/auth/password-reset/confirm", json={"token": token, "password": "freshly-chosen-passphrase"}
        )
        assert response.status_code == 400

    def test_requests_are_rate_limited(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        codes = [
            web.post(
                "/api/auth/password-reset/request", json={"email": "employee@acme.example.com"}
            ).status_code
            for _ in range(6)
        ]
        assert codes[-1] == 429


class TestInvitations:
    def invite(self, who: WebClient, email: str, role: str):  # type: ignore[no-untyped-def]
        return who.post("/api/invitations", json={"email": email, "role": role})

    def test_an_administrator_invites_and_the_person_joins(
        self, app, acme, outbox: MemoryEmailSender
    ) -> None:  # type: ignore[no-untyped-def]
        admin = new_web(app)
        admin.login("admin@acme.example.com")
        created = self.invite(admin, "New.Hire@Acme.Example.com", "manager")
        assert created.status_code == 201
        assert created.json()["email"] == "new.hire@acme.example.com"
        assert "accept_url" not in created.json()  # the wire format is camelCase
        link = created.json()["acceptUrl"]
        token = parse_qs(urlparse(link).query)["token"][0]
        assert outbox.outbox[-1].to == "new.hire@acme.example.com"
        assert token in outbox.outbox[-1].body

        newcomer = new_web(app)
        info = newcomer.get(f"/api/invitations/{token}")
        assert info.status_code == 200
        assert info.json()["companyName"] == "Acme"
        assert info.json()["role"] == "manager"

        joined = newcomer.post(
            f"/api/invitations/{token}/accept", json={"name": "Nia Hire", "password": "a-solid-passphrase-1"}
        )
        assert joined.status_code == 200
        assert joined.json()["role"] == "manager"
        assert joined.json()["emailVerified"] is True
        assert newcomer.get("/api/auth/me").json()["company"]["name"] == "Acme"

    def test_an_invitation_works_once(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        admin = new_web(app)
        admin.login("admin@acme.example.com")
        token = parse_qs(
            urlparse(self.invite(admin, "one@acme.example.com", "employee").json()["acceptUrl"]).query
        )["token"][0]
        payload = {"name": "One Person", "password": "a-solid-passphrase-1"}
        assert new_web(app).post(f"/api/invitations/{token}/accept", json=payload).status_code == 200
        again = new_web(app).post(f"/api/invitations/{token}/accept", json=payload)
        assert again.status_code == 404
        assert error_code(again) == "invitation_not_found"

    def test_expired_and_revoked_invitations_are_not_usable(
        self, app, acme, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        admin = new_web(app)
        admin.login("admin@acme.example.com")
        created = self.invite(admin, "late@acme.example.com", "employee").json()
        token = parse_qs(urlparse(created["acceptUrl"]).query)["token"][0]
        assert admin.delete(f"/api/invitations/{created['id']}").status_code == 204
        assert new_web(app).get(f"/api/invitations/{token}").status_code == 404

        fresh = self.invite(admin, "slow@acme.example.com", "employee").json()
        fresh_token = parse_qs(urlparse(fresh["acceptUrl"]).query)["token"][0]
        later = timeutil.utcnow() + timedelta(days=4)
        monkeypatch.setattr(timeutil, "utcnow", lambda: later)
        assert new_web(app).get(f"/api/invitations/{fresh_token}").status_code == 404

    def test_knowing_the_company_domain_is_not_enough_to_join(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        # There is no self-service sign-up route, and a guessed token cannot be accepted.
        assert web.post(
            "/api/auth/register", json={"email": "me@acme.example.com", "password": PASSWORD}
        ).status_code in {404, 405}
        guess = web.post(
            "/api/invitations/not-a-real-token-1234567890/accept",
            json={"name": "Mallory X", "password": PASSWORD},
        )
        assert guess.status_code == 404

    def test_roles_limit_who_can_invite_whom(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        assert self.invite(manager, "a@acme.example.com", "employee").status_code == 201
        assert self.invite(manager, "b@acme.example.com", "manager").status_code == 403
        assert self.invite(manager, "c@acme.example.com", "admin").status_code == 403

        employee = new_web(app)
        employee.login("employee@acme.example.com")
        assert self.invite(employee, "d@acme.example.com", "employee").status_code == 403
        assert employee.get("/api/invitations").status_code == 403

    def test_cannot_invite_someone_who_already_has_an_account(self, app, acme, globex) -> None:  # type: ignore[no-untyped-def]
        admin = new_web(app)
        admin.login("admin@acme.example.com")
        response = self.invite(admin, "admin@globex.example.com", "employee")
        assert response.status_code == 409
        assert error_code(response) == "account_exists"

    def test_a_weak_password_is_rejected_and_the_invitation_survives(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        admin = new_web(app)
        admin.login("admin@acme.example.com")
        token = parse_qs(
            urlparse(self.invite(admin, "weak@acme.example.com", "employee").json()["acceptUrl"]).query
        )["token"][0]
        weak = new_web(app).post(
            f"/api/invitations/{token}/accept", json={"name": "Weak Pass", "password": "short"}
        )
        assert weak.status_code == 422
        db.expire_all()
        assert db.scalar(select(User).where(User.email == "weak@acme.example.com")) is None
        ok = new_web(app).post(
            f"/api/invitations/{token}/accept", json={"name": "Weak Pass", "password": "a-solid-passphrase-1"}
        )
        assert ok.status_code == 200

    def test_a_newer_invitation_replaces_the_older_one(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        admin = new_web(app)
        admin.login("admin@acme.example.com")
        first = parse_qs(
            urlparse(self.invite(admin, "dup@acme.example.com", "employee").json()["acceptUrl"]).query
        )["token"][0]
        self.invite(admin, "dup@acme.example.com", "employee")
        assert new_web(app).get(f"/api/invitations/{first}").status_code == 404
        pending = db.scalars(
            select(Invitation).where(
                Invitation.email == "dup@acme.example.com", Invitation.revoked_at.is_(None)
            )
        ).all()
        assert len(pending) == 1

    def test_members_lists_only_the_callers_company(self, app, acme, globex) -> None:  # type: ignore[no-untyped-def]
        employee = new_web(app)
        employee.login("employee@acme.example.com")
        members = employee.get("/api/members").json()
        # Names tell the companies apart (Globex's people have the same role names but other addresses), and
        # an
        # employee is never shown anyone's address.
        assert {m["name"] for m in members} == {"Admin", "Manager", "Employee"}
        assert all(m["email"] is None for m in members)
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        emails = {m["email"] for m in manager.get("/api/members").json()}
        assert emails == {"admin@acme.example.com", "manager@acme.example.com", "employee@acme.example.com"}


def test_first_administrator_can_be_bootstrapped(web: WebClient, db: Session) -> None:
    _company, admin = accounts.create_company_with_admin(
        db,
        company_name="Initech",
        email="Boss@Initech.Example.com",
        name="Bill Boss",
        password="a-solid-passphrase-1",
    )
    assert admin.email == "boss@initech.example.com"
    response = web.login("boss@initech.example.com", "a-solid-passphrase-1")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["emailVerified"] is True
    assert ORIGIN  # silence unused import in strict linters
