"""Encrypted, consistent PostgreSQL snapshots and isolated restore drills.

Only encrypted archives and non-sensitive status files reach /backups.
Database passwords are passed through the child environment, never argv.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"MEDIAHUB-BACKUP-1\n"
CHUNK = 1024 * 1024
ID = re.compile(r"^[0-9a-f]{32}$")
DEFAULTS = {"backup.enabled": True, "backup.hour": 3,
            "backup.daily": 7, "backup.weekly": 4, "backup.monthly": 6}


def root():
    path = Path(os.getenv("BACKUP_DIR", "/backups"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def stamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temp, "x", opener=lambda p, f: os.open(p, f, 0o600)) as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def archive_path(backup_id):
    if not ID.fullmatch(backup_id):
        raise ValueError("Invalid backup id")
    return root() / (backup_id + ".mhb")


def encryption_key(salt):
    key = base64.urlsafe_b64decode(os.environ["DATA_ENCRYPTION_KEY"])
    if len(key) != 32:
        raise ValueError("Invalid backup encryption key")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b"mediahub-backup-v1").derive(key)


def encrypt(source, target):
    salt, nonce = os.urandom(32), os.urandom(12)
    header = MAGIC + salt + nonce
    cipher = Cipher(algorithms.AES(encryption_key(salt)), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(header)
    target.write(header)
    while data := source.read(CHUNK):
        target.write(cipher.update(data))
    target.write(cipher.finalize())
    target.write(cipher.tag)


def decrypt(source, target):
    header = source.read(len(MAGIC) + 44)
    if len(header) != len(MAGIC) + 44 or not header.startswith(MAGIC):
        raise ValueError("Invalid backup format")
    salt, nonce = header[len(MAGIC):len(MAGIC) + 32], header[-12:]
    source.seek(0, 2)
    remaining = source.tell() - len(header) - 16
    if remaining < 0:
        raise ValueError("Truncated backup")
    source.seek(-16, 2)
    tag = source.read(16)
    source.seek(len(header))
    cipher = Cipher(algorithms.AES(encryption_key(salt)), modes.GCM(nonce, tag)).decryptor()
    cipher.authenticate_additional_data(header)
    while remaining:
        data = source.read(min(CHUNK, remaining))
        if not data:
            raise ValueError("Truncated backup")
        remaining -= len(data)
        target.write(cipher.update(data))
    target.write(cipher.finalize())  # Authentication must pass before using plaintext.


def checksum(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def pg_env(database=None):
    return {**os.environ, "PGHOST": os.getenv("POSTGRES_HOST", "postgres"),
            "PGPORT": os.getenv("POSTGRES_PORT", "5432"),
            "PGUSER": os.environ["POSTGRES_USER"],
            "PGPASSWORD": os.environ["POSTGRES_PASSWORD"],
            "PGDATABASE": database or os.environ["POSTGRES_DB"],
            "PGCONNECT_TIMEOUT": "10"}


def connect(database=None, **kwargs):
    import psycopg
    env = pg_env(database)
    return psycopg.connect(host=env["PGHOST"], port=env["PGPORT"], user=env["PGUSER"],
                           password=env["PGPASSWORD"], dbname=env["PGDATABASE"],
                           connect_timeout=10, **kwargs)


def run_pg(args, database=None):
    # Errors are deliberately codes, never potentially sensitive server output.
    result = subprocess.run(args, env=pg_env(database), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=3600)
    if result.returncode:
        raise RuntimeError(f"{args[0]}_failed_{result.returncode}")


@contextmanager
def locked():
    import fcntl
    with (root() / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def table_counts(conn):
    from psycopg import sql
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    return {name: conn.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(name))).fetchone()[0]
            for (name,) in tables}


def create_backup(kind="manual", backup_id=None):
    backup_id = backup_id or uuid.uuid4().hex
    path = archive_path(backup_id)
    with locked():
        info_path = path.with_suffix(".json")
        if path.exists() and info_path.exists():
            previous = json.loads(info_path.read_text())
            if previous.get("status") == "success" and checksum(path) == previous.get("sha256"):
                return previous
        info = {"id": backup_id, "kind": kind, "created_at": stamp(), "status": "running"}
        atomic_json(info_path, info)
        partial = path.with_suffix(".partial")
        try:
            with tempfile.TemporaryDirectory(prefix="mediahub-backup-") as directory:
                directory = Path(directory)
                dump = directory / "database.dump"
                with connect() as conn:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
                    counts = table_counts(conn)
                    version = conn.execute("SHOW server_version_num").fetchone()[0]
                    schema = (conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                              if conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0] else None)
                    run_pg(["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                            "--snapshot=" + snapshot, "--file=" + str(dump)])
                manifest = {"format": 1, "created_at": info["created_at"], "counts": counts,
                            "postgres_version": version, "schema_version": schema, "id": backup_id}
                release_file = Path("/config/release.json")
                if release_file.is_file():
                    manifest["application_version"] = json.loads(release_file.read_text()).get("version")
                atomic_json(directory / "manifest.json", manifest)
                plain = directory / "archive.tar"
                with tarfile.open(plain, "w") as archive:
                    archive.add(dump, arcname="database.dump")
                    archive.add(directory / "manifest.json", arcname="manifest.json")
                    config = Path(os.getenv("BACKUP_ENV_FILE", "/config/mediahub.env"))
                    if not config.is_file():
                        raise ValueError("Backup configuration file is missing")
                    archive.add(config, arcname="config/mediahub.env", recursive=False)
                    secrets = Path(os.getenv("BACKUP_SECRETS_DIR", "/config/secrets"))
                    if secrets.is_dir():
                        for item in sorted(secrets.rglob("*")):
                            if item.is_symlink():
                                raise ValueError("Backup secret symlinks are not supported")
                            if item.is_file():
                                archive.add(item, arcname="config/secrets/" + str(item.relative_to(secrets)))
                with plain.open("rb") as source, open(partial, "wb", opener=lambda p, f: os.open(p, f, 0o600)) as target:
                    encrypt(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                partial.replace(path)
            info.update(status="success", finished_at=stamp(), sha256=checksum(path), bytes=path.stat().st_size)
            atomic_json(info_path, info)
            return info
        except Exception as exc:
            info.update(status="failed", finished_at=stamp(), error=type(exc).__name__)
            atomic_json(info_path, info)
            raise
        finally:
            partial.unlink(missing_ok=True)


@contextmanager
def opened_backup(backup_id):
    path = archive_path(backup_id)
    info = json.loads(path.with_suffix(".json").read_text())
    if info.get("status") != "success" or checksum(path) != info.get("sha256"):
        raise ValueError("Backup checksum failed")
    with tempfile.TemporaryDirectory(prefix="mediahub-restore-") as directory:
        directory = Path(directory)
        plain = directory / "archive.tar"
        with path.open("rb") as source, plain.open("wb") as target:
            decrypt(source, target)
        with tarfile.open(plain, "r:") as archive:
            names = set()
            for member in archive.getmembers():
                name = Path(member.name)
                if (not member.isfile() or name.is_absolute() or ".." in name.parts
                        or member.name in names or not (member.name in {"database.dump", "manifest.json", "config/mediahub.env"}
                        or member.name.startswith("config/secrets/"))):
                    raise ValueError("Unsafe backup member")
                names.add(member.name)
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600)
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest.get("id") != backup_id or manifest.get("format") != 1:
            raise ValueError("Backup identity mismatch")
        run_pg(["pg_restore", "--list", str(directory / "database.dump")])
        yield directory, manifest


def restore_drill(backup_id):
    from psycopg import sql
    database = "mediahub_drill_" + uuid.uuid4().hex
    with locked(), opened_backup(backup_id) as (directory, manifest):
        created = False
        try:
            with connect(autocommit=True) as conn:
                conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
                created = True
            run_pg(["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
                    "--dbname=" + database, str(directory / "database.dump")], database)
            with connect(database) as conn:
                if table_counts(conn) != manifest["counts"]:
                    raise ValueError("Restore row counts differ from snapshot")
            info_path = archive_path(backup_id).with_suffix(".json")
            info = json.loads(info_path.read_text())
            info["restore_verified_at"] = stamp()
            atomic_json(info_path, info)
            return info
        finally:
            if created:
                with connect(autocommit=True) as conn:
                    conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


def restore_database(backup_id, confirmation):
    # The host CLI performs the two interactive confirmations and stops writers.
    if confirmation != "RESTORE:" + os.environ["POSTGRES_DB"]:
        raise ValueError("Explicit target database confirmation required")
    from psycopg import sql
    restore_drill(backup_id)
    emergency = create_backup("emergency")
    replacement = "mediahub_recovery_" + uuid.uuid4().hex
    previous = "mediahub_before_" + uuid.uuid4().hex
    target = os.environ["POSTGRES_DB"]
    if target in {"template0", "template1", "postgres"}:
        raise ValueError("System databases cannot be restored")
    with locked(), opened_backup(backup_id) as (directory, manifest):
        with connect("template1", autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(replacement)))
        switched = False
        try:
            run_pg(["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
                    "--dbname=" + replacement, str(directory / "database.dump")], replacement)
            with connect(replacement) as conn:
                if table_counts(conn) != manifest["counts"]:
                    raise RuntimeError("Restore counts differ; original database unchanged")
            # Renames are transactional. Keep the old DB for recovery; never drop it.
            with connect("template1") as conn:
                if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=%s", (target,)).fetchone()[0]:
                    raise RuntimeError("Stop all application connections before restoring")
                conn.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(target), sql.Identifier(previous)))
                conn.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(replacement), sql.Identifier(target)))
            switched = True
        finally:
            if not switched:
                with connect("template1", autocommit=True) as conn:
                    conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(replacement)))
    return {"restored": backup_id, "emergency_backup": emergency["id"], "previous_database": previous}


def list_backups():
    records = []
    for path in root().glob("*.json"):
        if ID.fullmatch(path.stem):
            try:
                records.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
    return sorted(records, key=lambda row: row["created_at"], reverse=True)


def retention_ids(records, daily=7, weekly=4, monthly=6):
    good = [r for r in records if r.get("status") == "success"]
    keep = {good[0]["id"]} if good else set()
    for pattern, limit in (("%Y-%m-%d", daily), ("%G-%V", weekly), ("%Y-%m", monthly)):
        buckets = set()
        for record in good:
            bucket = datetime.fromisoformat(record["created_at"]).strftime(pattern)
            if bucket not in buckets and len(buckets) < limit:
                keep.add(record["id"])
                buckets.add(bucket)
    # Retain the latest proven restore even if subsequent backups have not been drilled.
    verified = next((r for r in good if r.get("restore_verified_at")), None)
    if verified:
        keep.add(verified["id"])
    return keep


def settings_values():
    with connect() as conn:
        rows = conn.execute("SELECT key, value_json FROM application_settings WHERE is_sensitive=false AND (category IN ('backups','notifications') OR key='quota.timezone')").fetchall()
    return {**DEFAULTS, **dict(rows)}


def notify(values, text):
    import requests
    if not values.get("notifications.enabled", False):
        return
    chat = values.get("notifications.chat_id")
    topic = values.get("notifications.topic.backups")
    if not chat or not topic:
        return
    try:
        requests.post("https://api.telegram.org/bot" + os.environ["TELEGRAM_BOT_TOKEN"] + "/sendMessage",
                      json={"chat_id": chat, "message_thread_id": topic, "text": text}, timeout=15)
    except Exception:
        pass


def daemon():
    requests_dir = root() / "requests"
    requests_dir.mkdir(mode=0o700, exist_ok=True)
    last_attempt = -3601
    values = {}
    last_error = None
    while True:
        try:
            values = settings_values()
            atomic_json(root() / "heartbeat.json", {"checked_at": stamp(), "healthy": last_error is None,
                        "enabled": values["backup.enabled"]})
            queued = sorted(requests_dir.glob("*.json"))
            records = list_backups()
            now = datetime.now(ZoneInfo(values.get("quota.timezone", "Asia/Tehran")))
            done_today = any(r.get("status") == "success" and datetime.fromisoformat(r["created_at"]).astimezone(now.tzinfo).date() == now.date() for r in records)
            scheduled = values["backup.enabled"] and now.hour >= values["backup.hour"] and (not done_today or last_error) and time.monotonic() - last_attempt > 3600
            if queued or scheduled:
                last_attempt = time.monotonic()
                request = queued[0] if queued else None
                try:
                    info = create_backup("manual" if request else "scheduled", request.stem if request else None)
                    recent_drill = any(r.get("restore_verified_at") and (datetime.now(timezone.utc) - datetime.fromisoformat(r["restore_verified_at"])).days < 7 for r in records)
                    if not recent_drill:
                        info = restore_drill(info["id"])
                    notify(values, "✅ MediaHub backup: " + info["id"] + ("\nRestore drill: OK" if info.get("restore_verified_at") else ""))
                    with locked():
                        records = list_backups()
                        keep = retention_ids(records, values["backup.daily"], values["backup.weekly"], values["backup.monthly"])
                        for record in records:
                            if record.get("status") == "success" and record["id"] not in keep:
                                archive_path(record["id"]).unlink(missing_ok=True)
                                archive_path(record["id"]).with_suffix(".json").unlink(missing_ok=True)
                    if last_error:
                        notify(values, "✅ MediaHub backup service recovered")
                    last_error = None
                    atomic_json(root() / "heartbeat.json", {"checked_at": stamp(), "healthy": True,
                                "enabled": values["backup.enabled"]})
                finally:
                    if request:
                        request.unlink(missing_ok=True)
        except Exception as exc:
            atomic_json(root() / "heartbeat.json", {"checked_at": stamp(), "healthy": False, "error": type(exc).__name__})
            if last_error != type(exc).__name__:
                notify(values, "❌ MediaHub backup failed: " + type(exc).__name__)
                last_error = type(exc).__name__
        time.sleep(60)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "verify", "restore", "list", "daemon", "health", "extract-config"])
    parser.add_argument("backup_id", nargs="?")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.command == "daemon":
        daemon()
        return
    if args.command == "health":
        value = json.loads((root() / "heartbeat.json").read_text())
        if not value["healthy"] or (datetime.now(timezone.utc) - datetime.fromisoformat(value["checked_at"])).total_seconds() > 7200:
            raise RuntimeError("Backup service is unhealthy")
        return
    if args.command == "create":
        result = create_backup()
    elif args.command == "list":
        result = list_backups()
    elif args.command == "verify":
        result = restore_drill(args.backup_id)
    elif args.command == "restore":
        result = restore_database(args.backup_id, args.confirm)
    else:
        if not args.output or Path(args.output).exists():
            raise ValueError("Choose a new private output directory")
        with opened_backup(args.backup_id) as (directory, manifest):
            shutil.copytree(directory / "config", args.output)
            Path(args.output).chmod(0o700)
        result = {"config_extracted": True, "schema_version": manifest.get("schema_version"),
                  "application_version": manifest.get("application_version")}
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("BACKUP_ERROR=" + type(exc).__name__, flush=True)
        raise SystemExit(1)
