"""Regression tests for the Phase F security review, part two: mail, invitations, what each role is shown, and
production configuration. Each was run against the code before the fix that goes with it."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import new_web


class TestMailIsSentToTheRealServer:
    def test_the_mail_servers_certificate_is_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import smtplib
        import ssl

        from app.services.email import OutgoingEmail, SmtpEmailSender

        seen: dict[str, object] = {}

        class FakeSMTP:
            def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
                seen["host"] = host

            def __enter__(self) -> FakeSMTP:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

            def starttls(self, *, context: ssl.SSLContext | None = None) -> None:
                seen["context"] = context

            def login(self, user: str, password: str) -> None:
                seen["login"] = True

            def send_message(self, message: object) -> None:
                seen["sent"] = True

        monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            secret_key="x" * 40,
            email_backend="smtp",
            smtp_host="smtp.example.test",
            smtp_username="user",
            smtp_password="pw",
        )
        SmtpEmailSender(settings).send(OutgoingEmail("a@example.com", "Hello", "Body"))
        context = seen["context"]
        # With no context given, Python would encrypt without checking who it is talking to, so anyone on the
        # path could read the password and every sign-in link.
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
        assert seen["login"] and seen["sent"]


class TestInvitations:
    def smtp_app(self, settings, engine, outbox, **changes):  # type: ignore[no-untyped-def]
        configured = settings.model_copy(
            update={"email_backend": "smtp", "smtp_host": "smtp.example.test", **changes}
        )
        return create_app(configured, engine=engine, email=outbox)

    def test_when_mail_really_goes_out_the_link_is_only_in_the_mail(
        self, settings, engine, outbox, acme
    ) -> None:  # type: ignore[no-untyped-def]
        # If the person who invites also got the link, they could accept it themselves, set a password, and
        # own an account verified as belonging to someone else's address.
        app = self.smtp_app(settings, engine, outbox)
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        created = manager.post(
            "/api/invitations", json={"email": "ceo@victim.example.com", "role": "employee"}
        )
        assert created.status_code == 201
        assert created.json()["acceptUrl"] is None
        assert (
            "register?token=" in outbox.outbox[-1].body and outbox.outbox[-1].to == "ceo@victim.example.com"
        )
        assert "register?token=" not in created.text

    def test_without_real_mail_the_link_is_still_shown_for_development(self, app, outbox, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        created = manager.post("/api/invitations", json={"email": "dev@acme.example.com", "role": "employee"})
        assert "register?token=" in (created.json()["acceptUrl"] or "")

    def test_invitations_are_limited_so_the_server_is_not_a_mail_relay(
        self, settings, engine, outbox, acme
    ) -> None:  # type: ignore[no-untyped-def]
        app = self.smtp_app(settings, engine, outbox, invites_per_hour=3)
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        codes = [
            manager.post(
                "/api/invitations", json={"email": f"p{n}@acme.example.com", "role": "employee"}
            ).status_code
            for n in range(5)
        ]
        assert codes == [201, 201, 201, 429, 429]
        assert len(outbox.outbox) == 3  # nothing was sent for the refused ones

    def test_the_limit_is_per_person_not_per_company(self, settings, engine, outbox, acme) -> None:  # type: ignore[no-untyped-def]
        app = self.smtp_app(settings, engine, outbox, invites_per_hour=2)
        first = new_web(app)
        first.login("manager@acme.example.com")
        second = new_web(app)
        second.login("admin@acme.example.com")
        for n in range(2):
            sent = first.post(
                "/api/invitations", json={"email": f"a{n}@acme.example.com", "role": "employee"}
            )
            assert sent.status_code == 201
        refused = first.post("/api/invitations", json={"email": "a9@acme.example.com", "role": "employee"})
        assert refused.status_code == 429
        other = second.post("/api/invitations", json={"email": "b0@acme.example.com", "role": "employee"})
        assert other.status_code == 201


class TestOnlyWhatARoleNeedsIsShown:
    def test_employees_see_names_of_colleagues_not_their_email_addresses(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        employee = new_web(app)
        employee.login("employee@acme.example.com")
        response = employee.get("/api/members")
        members = response.json()
        assert {m["name"] for m in members} >= {"Admin", "Manager", "Employee"}
        assert all(m["email"] is None for m in members)
        assert "@acme.example.com" not in response.text

    def test_managers_still_see_addresses_because_they_invite_and_assign(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        assert "admin@acme.example.com" in {m["email"] for m in manager.get("/api/members").json()}

    def test_setting_and_library_names_in_processing_notes_are_for_managers(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        employee = new_web(app)
        employee.login("employee@acme.example.com")
        assert manager.get("/api/processing").json()["notes"]  # what to do about it, for who can do it
        status = employee.get("/api/processing").json()
        assert status["notes"] == []
        # The yes/no facts the dashboard needs are still there.
        assert {"available", "transcription", "diarization", "speakerMatching", "queued"} <= set(status)


class TestProductionConfigurationGaps:
    good = {
        "env": "production",
        "database_url": "postgresql+psycopg://u:p@db/ask",
        "email_backend": "smtp",
        "smtp_host": "smtp.example.test",
        "web_origins": ["https://ask.example.test"],
        "web_base_url": "https://ask.example.test",
    }

    def make(self, **changes: object) -> Settings:
        return Settings(_env_file=None, secret_key="k" * 40, **{**self.good, **changes})  # type: ignore[call-arg]

    def test_a_complete_production_configuration_is_accepted(self) -> None:
        assert self.make().env == "production"

    def test_mail_kept_in_memory_is_refused(self) -> None:
        # It would quietly collect every reset and invitation link in an ever-growing list and send none.
        with pytest.raises(ValueError, match="memory"):
            self.make(email_backend="memory")

    @pytest.mark.parametrize(
        "origin", ["http://ask.example.test", "https://localhost:3000", "https://127.0.0.1"]
    )
    def test_origins_must_be_https_and_real(self, origin: str) -> None:
        with pytest.raises(ValueError, match="ASK_WEB_ORIGINS"):
            self.make(web_origins=[origin])

    @pytest.mark.parametrize("url", ["http://ask.example.test", "https://localhost:3000"])
    def test_the_address_in_emailed_links_must_be_https_and_real(self, url: str) -> None:
        with pytest.raises(ValueError, match="ASK_WEB_BASE_URL"):
            self.make(web_base_url=url)

    def test_development_is_not_held_to_it(self) -> None:
        Settings(_env_file=None, secret_key="k" * 40)  # type: ignore[call-arg]
        Settings(_env_file=None, secret_key="k" * 40, email_backend="memory")  # type: ignore[call-arg]
