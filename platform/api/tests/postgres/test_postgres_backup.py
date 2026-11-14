"""Back up a real PostgreSQL the way platform/deploy/backup.sh does (`pg_dump --format=custom`), restore it
into a fresh database the way the runbook says (`pg_restore --clean --if-exists`), and prove the restored copy
is the same data and that the application works on it.

This covers the DATABASE half of the backup procedure only. The scripts wrap these commands in
`docker compose`, and the recordings archive is a `tar` of a volume: neither of those was run (no Docker).

Needs `pg_dump`, `pg_restore` and `psql`: on the PATH, or in the folder named by ASK_PG_BIN."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine, make_url

from app.db import make_engine, make_session_factory
from app.main import create_app
from app.services.email import MemoryEmailSender
from app.worker import Worker
from tests.ai_doubles import make_provider
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member, make_wav
from tests.postgres.test_postgres import pg_app


def tool(name: str) -> str:
    folder = os.environ.get("ASK_PG_BIN")
    found = shutil.which(name, path=folder) if folder else shutil.which(name)
    if not found:
        pytest.skip(f"{name} not found (put PostgreSQL's bin folder on the PATH or set ASK_PG_BIN)")
    return found


def run(command: list[str], password: str | None) -> None:
    done = subprocess.run(
        command, env={**os.environ, "PGPASSWORD": password or ""}, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, f"{command[0]} failed: {done.stderr[-600:]}"


def row_counts(engine: Engine) -> dict[str, int]:
    names = [n for n in inspect(engine).get_table_names() if n != "alembic_version"]
    with engine.connect() as connection:
        return {n: connection.execute(text(f'SELECT count(*) FROM "{n}"')).scalar_one() for n in names}  # noqa: S608


def test_a_custom_format_dump_restores_into_a_fresh_database_with_the_same_data_and_a_working_app(
    settings, pg_engine: Engine, pg_url: str, tmp_path: Path
) -> None:
    pg_dump, pg_restore, psql = tool("pg_dump"), tool("pg_restore"), tool("psql")
    source = make_url(pg_url)
    host, port, user, password = (
        source.host or "127.0.0.1",
        str(source.port or 5432),
        source.username or "postgres",
        source.password,
    )
    restored_name = f"{source.database}_restored_{uuid.uuid4().hex[:6]}"  # still contains verify or test

    # Real data made the way the product makes it: sign in, pair a locket, upload, process.
    app = pg_app(settings, pg_engine, pg_url)
    with TestClient(app):
        with make_session_factory(pg_engine)() as db:
            company = add_company(db, "Acme")
            add_member(db, company, "manager@acme.example.com", "manager")
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        _, locket = provision_device(app, manager)
        recordings = []
        for n in range(3):
            stored = locket.upload(f"clip-{n:04d}-{uuid.uuid4().hex[:6]}", make_wav(9.0))
            assert stored.status_code == 201, stored.text
            recordings.append(stored.json()["recordingId"])
        worker = Worker(
            app.state.session_factory, app.state.settings, make_provider(), app.state.storage, "w"
        )
        while worker.work_once():
            pass
    before = row_counts(pg_engine)
    assert before["recordings"] == 3 and before["transcripts"] == 3 and before["audit_events"] > 0

    dump = tmp_path / "database.dump"
    run(
        [
            pg_dump,
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            source.database or "",
            "--format=custom",
            "-f",
            str(dump),
        ],
        password,
    )
    assert dump.stat().st_size > 1000

    run(
        [
            psql,
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            "postgres",
            "-c",
            f"CREATE DATABASE {restored_name}",
        ],
        password,
    )
    restored_url = source.set(database=restored_name)
    try:
        run(
            [
                pg_restore,
                "-h",
                host,
                "-p",
                port,
                "-U",
                user,
                "-d",
                restored_name,
                "--clean",
                "--if-exists",
                str(dump),
            ],
            password,
        )
        restored = make_engine(restored_url.render_as_string(hide_password=False))
        try:
            assert row_counts(restored) == before  # every table, every row

            # The application works on the copy: the manager signs in (password hash restored) and reads
            # a transcript.
            again = create_app(
                settings.model_copy(
                    update={"database_url": restored_url.render_as_string(hide_password=False)}
                ),
                engine=restored,
                email=MemoryEmailSender(),
            )
            with TestClient(again):
                web = new_web(again)
                web.login("manager@acme.example.com")
                detail = web.get(f"/api/recordings/{recordings[0]}").json()
                assert detail["state"] == "completed" and detail["transcript"]["segments"]
        finally:
            restored.dispose()
    finally:
        run(
            [
                psql,
                "-h",
                host,
                "-p",
                port,
                "-U",
                user,
                "-d",
                "postgres",
                "-c",
                f"DROP DATABASE IF EXISTS {restored_name}",
            ],
            password,
        )
