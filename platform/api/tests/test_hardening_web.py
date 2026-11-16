"""Security headers on API responses, and the cleaning of text people type.

Added in the website security pass. Each test here fails against the code as it stood before that pass.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.main import create_app
from app.models import ContactMessage
from app.schemas.common import clean_line, clean_text
from app.security.headers import SecurityHeadersMiddleware
from app.services.email import MemoryEmailSender
from tests.helpers import WebClient

LS, PS = chr(0x2028), chr(0x2029)  # line and paragraph separators

EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "same-origin",
    "cache-control": "no-store",
}


class TestSecurityHeaders:
    def test_a_normal_answer_carries_them(self, web: WebClient) -> None:
        response = web.get("/api/health")
        for name, value in EXPECTED.items():
            assert response.headers.get(name) == value, name
        # Data answers may not be framed, scripted or used to submit a form from.
        policy = response.headers["content-security-policy"]
        assert "default-src 'none'" in policy and "frame-ancestors 'none'" in policy

    def test_so_does_an_error(self, web: WebClient) -> None:
        for response in (web.get("/api/summary"), web.get("/api/no-such-thing")):
            assert response.status_code in {401, 404}
            for name, value in EXPECTED.items():
                assert response.headers.get(name) == value, (response.status_code, name)

    def test_so_does_a_refused_oversized_request(self, web: WebClient) -> None:
        response = web.post("/api/contact", content=b"x" * (2 * 1024 * 1024), csrf=False)
        assert response.status_code == 413
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("cache-control") == "no-store"

    def test_sign_in_answers_are_never_stored_by_a_cache(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        from tests.helpers import PASSWORD

        response = web.post("/api/auth/login", json={"email": "admin@acme.example.com", "password": PASSWORD})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        me = web.get("/api/auth/me")
        assert me.status_code == 200 and me.headers["cache-control"] == "no-store"

    def test_a_route_keeps_the_headers_it_chose(self) -> None:
        """Audio sets `private, no-store` and the live stream `no-cache, no-transform`: not replaced."""

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            response = PlainTextResponse(
                "x", headers={"Cache-Control": "private, no-store", "X-Frame-Options": "SAMEORIGIN"}
            )
            await response(scope, receive, send)

        wrapped: ASGIApp = SecurityHeadersMiddleware(app)
        response = TestClient(wrapped).get("/")
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_the_data_policy_is_only_for_data(self) -> None:
        """A page of documentation (development only) needs scripts; an audio file has to be playable."""

        async def html(scope: Scope, receive: Receive, send: Send) -> None:
            await PlainTextResponse("<p>x</p>", media_type="text/html")(scope, receive, send)

        async def audio(scope: Scope, receive: Receive, send: Send) -> None:
            await PlainTextResponse("RIFF", media_type="audio/wav")(scope, receive, send)

        for inner in (html, audio):
            response = TestClient(SecurityHeadersMiddleware(inner)).get("/")
            assert "content-security-policy" not in response.headers
            assert response.headers["x-content-type-options"] == "nosniff"

    def test_the_live_stream_keeps_its_own_cache_rule_and_gets_the_rest(self, file_app) -> None:  # type: ignore[no-untyped-def]
        from tests.helpers import PASSWORD, add_company, add_member

        with TestClient(file_app) as client:
            web = WebClient(client)
            factory = file_app.state.session_factory
            with factory() as db:
                company = add_company(db, "Acme")
                add_member(db, company, "admin@acme.example.com", "admin")
            assert (
                web.post(
                    "/api/auth/login", json={"email": "admin@acme.example.com", "password": PASSWORD}
                ).status_code
                == 200
            )
            with client.stream("GET", "/api/events", headers={"Origin": "http://localhost:3000"}) as stream:
                assert stream.status_code == 200
                assert stream.headers["cache-control"] == "no-cache, no-transform"
                assert stream.headers["x-content-type-options"] == "nosniff"
                assert "default-src 'none'" in stream.headers["content-security-policy"]


class TestCleaningText:
    def test_a_line_break_in_a_name_becomes_a_space(self) -> None:
        assert clean_line("Eve\r\nBcc: victim@example.com") == "Eve Bcc: victim@example.com"
        assert clean_line("  Priya \t  Nair \n") == "Priya Nair"
        assert clean_line(f"A{LS}B{PS}C\x00D\x1b[31m") == "A B C D [31m"

    def test_right_to_left_overrides_cannot_disguise_a_name(self) -> None:
        # U+202E reverses what follows: "Sam" + override + "reviled" renders as "Sam" + "devirer".
        assert clean_line("Sam‮reviled") == "Samreviled"
        assert clean_line("a⁦b⁩c") == "abc"

    def test_ordinary_names_and_emoji_survive(self) -> None:
        for name in ("Zoë Müller", "José Álvarez-O'Brien", "李小龍", "Priya Nair", "Sam 👩‍💻 Okafor"):
            assert clean_line(name) == name

    def test_unicode_is_composed_once(self) -> None:
        assert clean_line("Zoë") == "Zoë"

    def test_a_message_keeps_its_lines_but_loses_control_characters(self) -> None:
        assert clean_text(f"one\r\ntwo\rthree{LS}four\x00\x07\tfive  ") == "one\ntwo\nthree\nfour\tfive"

    def test_other_values_pass_through_untouched(self) -> None:
        assert clean_line(None) is None
        assert clean_line(7) == 7
        assert clean_text(["x"]) == ["x"]


CONTACT = {
    "name": "Sam Okafor",
    "email": "sam@example.com",
    "company": "Northwind",
    "message": "We would like a pilot for ten lockets next quarter.",
    "website": "",
    "elapsedMs": 9000,
}


class TestTheContactFormIsCleaned:
    def test_a_name_with_a_line_break_is_stored_and_forwarded_on_one_line(
        self, settings, engine, db: Session, outbox: MemoryEmailSender
    ) -> None:  # type: ignore[no-untyped-def]
        configured = settings.model_copy(update={"contact_recipient": "team@example.com"})
        client = WebClient(TestClient(create_app(configured, engine=engine, email=outbox)))
        response = client.post(
            "/api/contact", json={**CONTACT, "name": "Eve\r\nBcc: victim@example.com", "company": "A\nB"}
        )
        assert response.status_code == 204
        stored = db.scalar(select(ContactMessage))
        assert stored is not None
        assert stored.name == "Eve Bcc: victim@example.com" and stored.company == "A B"
        sent = outbox.outbox[-1]
        assert "\n" not in sent.subject and "\r" not in sent.subject

    def test_a_name_of_only_control_characters_is_refused_not_stored_empty(self, web: WebClient) -> None:
        response = web.post("/api/contact", json={**CONTACT, "name": "\n\n\t‮"})
        assert response.status_code == 422
        assert "name" in response.json()["error"]["details"]

    def test_the_real_mail_library_accepts_what_is_forwarded(self, settings) -> None:  # type: ignore[no-untyped-def]
        """The subject is built from the cleaned name; the standard library refuses line breaks in headers."""
        from email.message import EmailMessage

        from app.schemas.people import ContactIn

        body = ContactIn(**{**CONTACT, "name": "Eve\r\nBcc: x@example.com"})
        mail = EmailMessage()
        mail["Subject"] = f"ASK contact form: {body.name.strip()}"  # raised ValueError before the cleaning
        assert "Bcc" not in mail

    def test_a_device_or_person_name_is_cleaned_too(self, web: WebClient, acme) -> None:  # type: ignore[no-untyped-def]
        from tests.helpers import PASSWORD

        assert (
            web.post(
                "/api/auth/login", json={"email": "manager@acme.example.com", "password": PASSWORD}
            ).status_code
            == 200
        )
        created = web.post("/api/devices", json={"name": "Front\r\ndesk‮ one"})
        assert created.status_code == 201, created.text
        assert created.json()["deviceName"] == "Front desk one"
        person = web.post("/api/people", json={"displayName": "Ana\nLopez"})
        assert person.status_code == 201, person.text
        assert person.json()["displayName"] == "Ana Lopez"


class TestProductionSwitches:
    """What "turn off debug mode" means here: in production the interactive docs and the schema are gone, and
    cookies are Secure. (There is no debug flag to leave on: the application never starts uvicorn in reload or
    debug mode, and error answers never carry details.)"""

    def test_docs_are_off_and_cookies_are_secure(
        self,
        settings,
        engine,
        outbox: MemoryEmailSender,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        from tests.helpers import PASSWORD

        production = settings.model_copy(update={"env": "production", "cookie_secure": None})
        assert production.secure_cookies is True
        client = WebClient(TestClient(create_app(production, engine=engine, email=outbox)))
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/openapi.json").status_code == 404
        response = client.post(
            "/api/auth/login", json={"email": "admin@acme.example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
        for cookie in response.headers.get_list("set-cookie"):
            assert "Secure" in cookie, cookie
        assert "HttpOnly" in next(
            c for c in response.headers.get_list("set-cookie") if c.startswith("ask_session")
        )

    def test_an_unexpected_error_tells_the_caller_nothing(self, app, web: WebClient) -> None:  # type: ignore[no-untyped-def]
        @app.get("/api/_boom")
        def boom() -> None:
            raise RuntimeError("secret detail: /srv/data/recordings/abc password=hunter2")

        response = TestClient(app, raise_server_exceptions=False).get("/api/_boom")
        assert response.status_code == 500
        assert response.json() == {
            "error": {"code": "internal_error", "message": "Something went wrong on our side."}
        }
        assert (
            "hunter2" not in response.text
            and "/srv/" not in response.text
            and "Traceback" not in response.text
        )
