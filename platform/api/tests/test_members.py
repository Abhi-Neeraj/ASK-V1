"""Managing who is in the company: change a role, deactivate or reactivate someone, sign them out.

Until this existed nothing ever set `is_active`, so a person who left, or whose account was taken over,
kept their
access until someone edited the database by hand."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, AuthSession, Membership
from tests.conftest import new_web
from tests.helpers import PASSWORD, add_member


def signed_in(app, email: str):  # type: ignore[no-untyped-def]
    web = new_web(app)
    assert web.login(email).status_code == 200
    return web


class TestWhoMayManageMembers:
    def test_only_an_administrator(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        target = acme["employee"].id
        for email in ("manager@acme.example.com", "employee@acme.example.com"):
            web = signed_in(app, email)
            assert web.patch(f"/api/members/{target}", json={"role": "manager"}).status_code == 403
            assert web.delete(f"/api/members/{target}/sessions").status_code == 403

    def test_nobody_changes_their_own_role_or_access(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        admin = signed_in(app, "admin@acme.example.com")
        me = acme["admin"].id
        for body in ({"role": "employee"}, {"isActive": False}):
            response = admin.patch(f"/api/members/{me}", json=body)
            assert response.status_code == 409 and response.json()["error"]["code"] == "cannot_change_self"
        # So there is always an administrator left to do the next thing.
        assert admin.get("/api/auth/me").status_code == 200

    def test_an_empty_change_is_refused(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        admin = signed_in(app, "admin@acme.example.com")
        assert admin.patch(f"/api/members/{acme['employee'].id}", json={}).status_code == 422
        assert admin.patch(f"/api/members/{acme['employee'].id}", json={"role": "owner"}).status_code == 422

    def test_another_companys_people_are_not_found(self, app, acme, globex) -> None:  # type: ignore[no-untyped-def]
        admin = signed_in(app, "admin@acme.example.com")
        stranger = globex["employee"].id
        assert admin.patch(f"/api/members/{stranger}", json={"role": "admin"}).status_code == 404
        assert admin.patch(f"/api/members/{stranger}", json={"isActive": False}).status_code == 404
        assert admin.delete(f"/api/members/{stranger}/sessions").status_code == 404
        assert admin.patch(f"/api/members/{uuid.uuid4()}", json={"role": "admin"}).status_code == 404

    def test_an_account_shared_with_another_company_cannot_be_switched_off_from_here(
        self,
        app,
        db: Session,
        acme,
        globex,  # type: ignore[no-untyped-def]
    ) -> None:
        # Deactivating is account-wide, so it would lock the person out of a company this admin does not run.
        shared = add_member(db, acme["company"], "shared@example.com", "employee")
        db.add(Membership(user_id=shared.id, company_id=globex["company"].id, role="employee"))
        db.commit()
        admin = signed_in(app, "admin@acme.example.com")
        response = admin.patch(f"/api/members/{shared.id}", json={"isActive": False})
        assert response.status_code == 409 and response.json()["error"]["code"] == "shared_account"
        # Their role here is this company's business, and can still change.
        assert admin.patch(f"/api/members/{shared.id}", json={"role": "manager"}).status_code == 200


class TestChangingARole:
    def test_it_takes_effect_at_once_and_is_audited(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        staff = signed_in(app, "employee@acme.example.com")
        assert staff.post("/api/people", json={"displayName": "Priya Nair"}).status_code == 403

        admin = signed_in(app, "admin@acme.example.com")
        changed = admin.patch(f"/api/members/{acme['employee'].id}", json={"role": "manager"})
        assert changed.status_code == 200 and changed.json()["role"] == "manager"

        # The person's existing sign-in now has the new role, with no need to sign in again.
        assert staff.post("/api/people", json={"displayName": "Priya Nair"}).status_code == 201
        event = db.scalars(select(AuditEvent).where(AuditEvent.action == "member.role_changed")).one()
        assert event.actor_user_id == acme["admin"].id and event.object_id == str(acme["employee"].id)
        assert event.detail == {"from": "employee", "to": "manager"}

    def test_taking_a_role_away_also_takes_effect_at_once(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        boss = signed_in(app, "manager@acme.example.com")
        assert boss.get("/api/invitations").status_code == 200
        signed_in(app, "admin@acme.example.com").patch(
            f"/api/members/{acme['manager'].id}", json={"role": "employee"}
        )
        assert boss.get("/api/invitations").status_code == 403

    def test_setting_the_role_someone_already_has_changes_nothing_and_says_nothing(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        admin = signed_in(app, "admin@acme.example.com")
        assert admin.patch(f"/api/members/{acme['manager'].id}", json={"role": "manager"}).status_code == 200
        assert db.scalars(select(AuditEvent).where(AuditEvent.action == "member.role_changed")).all() == []


class TestSwitchingSomeoneOff:
    def test_their_sign_in_stops_working_straight_away_and_they_cannot_return(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        leaver = signed_in(app, "employee@acme.example.com")
        assert leaver.get("/api/auth/me").status_code == 200
        admin = signed_in(app, "admin@acme.example.com")

        done = admin.patch(f"/api/members/{acme['employee'].id}", json={"isActive": False})
        assert done.status_code == 200 and done.json()["isActive"] is False

        assert leaver.get("/api/auth/me").status_code == 401  # the session that was already open
        assert new_web(app).login("employee@acme.example.com", PASSWORD).status_code == 401  # and any new one
        db.expire_all()
        sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == acme["employee"].id)).all()
        assert sessions and all(s.revoked_at is not None for s in sessions)
        assert "member.deactivated" in set(db.scalars(select(AuditEvent.action)))

    def test_reactivating_lets_them_sign_in_again(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        admin = signed_in(app, "admin@acme.example.com")
        admin.patch(f"/api/members/{acme['employee'].id}", json={"isActive": False})
        assert new_web(app).login("employee@acme.example.com").status_code == 401
        back = admin.patch(f"/api/members/{acme['employee'].id}", json={"isActive": True})
        assert back.status_code == 200 and back.json()["isActive"] is True
        assert new_web(app).login("employee@acme.example.com").status_code == 200
        assert "member.reactivated" in set(db.scalars(select(AuditEvent.action)))

    def test_switching_off_a_manager_ends_their_access_to_manager_things(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        boss = signed_in(app, "manager@acme.example.com")
        signed_in(app, "admin@acme.example.com").patch(
            f"/api/members/{acme['manager'].id}", json={"isActive": False}
        )
        assert boss.get("/api/invitations").status_code == 401

    def test_signing_someone_out_without_switching_them_off(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        # For a laptop left logged in, or a session you suspect: they can sign in again with their password.
        staff = signed_in(app, "employee@acme.example.com")
        admin = signed_in(app, "admin@acme.example.com")
        assert admin.delete(f"/api/members/{acme['employee'].id}/sessions").status_code == 204
        assert staff.get("/api/auth/me").status_code == 401
        assert new_web(app).login("employee@acme.example.com").status_code == 200
        assert "member.sessions_ended" in set(db.scalars(select(AuditEvent.action)))

    def test_managers_can_see_who_is_switched_off_and_everyone_else_cannot(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        add_member(db, acme["company"], "second@acme.example.com", "employee", name="Second Employee")
        signed_in(app, "admin@acme.example.com").patch(
            f"/api/members/{acme['employee'].id}", json={"isActive": False}
        )

        flagged = {
            m["name"]: m["isActive"]
            for m in signed_in(app, "manager@acme.example.com").get("/api/members").json()
        }
        assert flagged["Employee"] is False and flagged["Manager"] is True
        colleagues = {m["name"] for m in signed_in(app, "second@acme.example.com").get("/api/members").json()}
        assert "Employee" not in colleagues and "Manager" in colleagues

    def test_a_locket_cannot_be_given_to_someone_who_is_switched_off(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import provision_device

        admin = signed_in(app, "admin@acme.example.com")
        device, _ = provision_device(app, admin, "Spare")
        admin.patch(f"/api/members/{acme['employee'].id}", json={"isActive": False})
        refused = admin.patch(
            f"/api/devices/{device['id']}", json={"assignedUserId": str(acme["employee"].id)}
        )
        assert refused.status_code == 422
        assert (
            admin.patch(
                f"/api/devices/{device['id']}", json={"assignedUserId": str(acme["manager"].id)}
            ).status_code
            == 200
        )
