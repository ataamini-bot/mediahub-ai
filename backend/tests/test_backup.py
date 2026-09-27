import io
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet

from app.backup import engine as backup


def test_backup_stream_is_authenticated_and_handles_multiple_chunks(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    payload = os.urandom(backup.CHUNK * 2 + 157)
    encrypted = io.BytesIO()
    backup.encrypt(io.BytesIO(payload), encrypted)
    stored = encrypted.getvalue()
    assert payload[:100] not in stored
    restored = io.BytesIO()
    backup.decrypt(io.BytesIO(stored), restored)
    assert restored.getvalue() == payload
    damaged = bytearray(stored)
    damaged[len(backup.MAGIC) + 100] ^= 1
    for invalid in (bytes(damaged), stored[:-20], stored + b"extra"):
        with pytest.raises((InvalidTag, ValueError)):
            backup.decrypt(io.BytesIO(invalid), io.BytesIO())
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(InvalidTag):
        backup.decrypt(io.BytesIO(stored), io.BytesIO())


def test_retention_keeps_daily_weekly_monthly_and_latest_proven_restore():
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    records = [{"id": str(i), "status": "success", "created_at": (now - timedelta(days=i)).isoformat()}
               for i in range(400)]
    records[-1]["restore_verified_at"] = now.isoformat()
    keep = backup.retention_ids(records)
    assert all(str(i) in keep for i in range(7))
    assert "399" in keep
    assert len(keep) <= 7 + 4 + 6 + 1
    assert "200" not in keep


@pytest.mark.parametrize("value", ["../other", "/etc/passwd", "x" * 32, "", "a" * 33])
def test_backup_ids_cannot_escape_storage(value, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        backup.archive_path(value)


@pytest.mark.skipif(os.getenv("RUN_BACKUP_INTEGRATION") != "1", reason="Requires isolated PostgreSQL and pg_dump/pg_restore (enabled in CI)")
def test_real_snapshot_encryption_restore_and_safe_database_switch(tmp_path, monkeypatch):
    from psycopg import sql
    assert shutil.which("pg_dump") and shutil.which("pg_restore")
    source = "mediahub_backup_test_" + uuid.uuid4().hex
    administrator = backup.connect(autocommit=True)
    administrator.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(source)))
    monkeypatch.setenv("POSTGRES_DB", source)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "archives"))
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config = tmp_path / "mediahub.env"
    config.write_text("TEST_SECRET=must-stay-encrypted\n")
    monkeypatch.setenv("BACKUP_ENV_FILE", str(config))
    monkeypatch.setenv("BACKUP_SECRETS_DIR", str(tmp_path / "unused-secrets"))
    old_database = None
    try:
        with backup.connect() as conn:
            conn.execute("CREATE TABLE marker(id integer PRIMARY KEY, content text)")
            conn.execute("INSERT INTO marker VALUES (42, 'saved-data')")
        result = backup.create_backup()
        encrypted = backup.archive_path(result["id"]).read_bytes()
        assert b"must-stay-encrypted" not in encrypted
        assert backup.restore_drill(result["id"])["restore_verified_at"]
        with backup.opened_backup(result["id"]) as (directory, manifest):
            assert (directory / "config/mediahub.env").read_text() == config.read_text()
            assert manifest["counts"] == {"marker": 1}
        with backup.connect() as conn:
            conn.execute("UPDATE marker SET id=99")
        with pytest.raises(ValueError):
            backup.restore_database(result["id"], "WRONG")
        restored = backup.restore_database(result["id"], "RESTORE:" + source)
        old_database = restored["previous_database"]
        with backup.connect() as conn:
            assert conn.execute("SELECT id FROM marker").fetchone()[0] == 42
        with backup.connect(old_database) as conn:
            assert conn.execute("SELECT id FROM marker").fetchone()[0] == 99
        assert backup.archive_path(restored["emergency_backup"]).is_file()
        assert administrator.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'mediahub_drill_%'").fetchone()[0] == 0
    finally:
        for name in filter(None, (source, old_database)):
            administrator.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        administrator.close()
