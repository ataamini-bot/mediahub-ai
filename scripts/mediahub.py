#!/usr/bin/env python3
"""Interactive host console for a single MediaHub Compose installation."""
import argparse
import base64
import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ops_common import (BotAPI, OperationError, ask, confirm, private_write,
                        read_env, read_json, run, save_json, write_env)
from telegram_setup import configure_forum, required_channel

APPS = ("backend", "worker", "monitor", "backup", "bot")
YOUTUBE_PROVIDER = "bgutil-provider"
VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?")
ROOT = Path(os.environ.get("MEDIAHUB_DIR", "/opt/mediahub-ai")).resolve()


class Console:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.state = self.root / ".mediahub"
        self.state.mkdir(mode=0o700, exist_ok=True)
        self.state.chmod(0o700)

    def git(self, *args):
        return run(["git", *args], cwd=self.root, capture=True)

    def compose(self, *args, **kwargs):
        return run(["docker", "compose", "--project-name", "mediahub-ai", *args], cwd=self.root, **kwargs)

    def local(self, payload):
        return json.loads(self.compose("run", "--rm", "--no-deps", "-T", "-e", "DEBUG=false", "backend",
                                      "python", "-m", "app.services.installation",
                                      data=json.dumps(payload), capture=True))

    def clean(self):
        if self.git("status", "--porcelain"):
            raise OperationError("Project files have local changes. Save and review them before continuing.")
        override = self.root / "docker-compose.override.yml"
        if override.exists() and not read_json(override, {}).get("x-mediahub-managed"):
            raise OperationError("Review the custom Compose override before installation.")

    def images_override(self, images):
        save_json(self.root / "docker-compose.override.yml", {"x-mediahub-managed": True,
            "services": {name: {"image": image} for name, image in images.items()}})

    def has_youtube_provider(self):
        # Recovery may select an older Compose file without this service.
        return YOUTUBE_PROVIDER in self.compose("config", "--services", capture=True).splitlines()

    def start_youtube_provider(self):
        if self.has_youtube_provider():
            self.compose("up", "-d", "--no-deps", "--no-build", "--pull", "never",
                         "--wait", "--wait-timeout", "120", YOUTUBE_PROVIDER)

    def prepare(self, version=None):
        """Pull versioned release images, checking their source revision; candidates build locally."""
        if version and VERSION.fullmatch(version):
            registry = read_json(self.root / "secrets/update-source.json", {}).get("registry", "ghcr.io/ataamini-bot/mediahub-ai")
            if not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9/_-]+", registry):
                raise OperationError("Invalid image registry")
            images = {}
            head = self.git("rev-parse", "HEAD")
            for kind in ("backend", "bot", "backup"):
                tag = f"{registry}/{kind}:{version}"
                run(["docker", "pull", tag])
                info = json.loads(run(["docker", "image", "inspect", tag], capture=True))[0]
                if info.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision") != head:
                    raise OperationError("Release image revision differs from the selected source commit")
                digest = next((x for x in info.get("RepoDigests", []) if x.startswith(registry + "/" + kind + "@sha256:")), None)
                if not digest:
                    raise OperationError("Release image digest is missing")
                images[kind] = digest
            images["worker"] = images["monitor"] = images["backend"]
            self.images_override(images)
        else:
            # Build explicit local tags so an old digest override cannot tag a build by digest.
            suffix = self.git("rev-parse", "--short=12", "HEAD")
            self.images_override({name: f"mediahub-ai-{name}:candidate-{suffix}" for name in APPS})
            self.compose("build", *APPS)
        if self.has_youtube_provider():
            # Fetch before stopping the running application; a registry outage
            # must not cause downtime or replace the current helper container.
            self.compose("pull", YOUTUBE_PROVIDER)
        self.compose("run", "--rm", "--no-deps", "-T", "backend", "python", "-m", "compileall", "-q", "app")
        self.compose("run", "--rm", "--no-deps", "-T", "bot", "python", "-m", "compileall", "-q", "app")

    def idle(self):
        sql = "SELECT count(*) FROM download_jobs WHERE status::text IN ('PENDING','PROCESSING','PAUSED')"
        count = self.compose("exec", "-T", "postgres", "sh", "-c",
            'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
            "sh", sql, capture=True)
        if count != "0":
            raise OperationError(f"ACTIVE_OR_PAUSED_DOWNLOAD_JOBS={count}; finish or cancel these requests first.")

    def health(self):
        for attempt in range(60):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            time.sleep(2)
        else:
            raise OperationError("Backend health check failed")
        for service in APPS:
            info = json.loads(run(["docker", "inspect", "mediahub-" + service], capture=True))[0]
            if info["State"]["Status"] != "running" or info["RestartCount"]:
                raise OperationError(f"Unhealthy service: {service}")
        if self.has_youtube_provider():
            self.compose("exec", "-T", "backend", "python", "-m", "app.services.youtube_health")
        print("APPLICATION_HEALTH=OK")

    def start_apps(self):
        self.start_youtube_provider()
        self.compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", *APPS)
        time.sleep(5)
        self.health()

    def backup(self, verify=False):
        info = json.loads(self.compose("run", "--rm", "--no-deps", "-T", "backup", "create", capture=True))
        if not re.fullmatch(r"[a-f0-9]{32}", info.get("id", "")):
            raise OperationError("Invalid backup result")
        if verify:
            self.compose("run", "--rm", "--no-deps", "-T", "backup", "verify", info["id"])
        print("BACKUP_ID=" + info["id"])
        return info

    def activate(self, *, bootstrap=True):
        self.compose("run", "--rm", "--no-deps", "-T", "backend", "alembic", "upgrade", "head")
        if bootstrap:
            self.local({"action": "bootstrap"})
        self.start_apps()
        self.backup(verify=True)
        self.register()
        save_json(self.state / "install.json", {"phase": "complete", "head": self.git("rev-parse", "HEAD")})
        print("INSTALLATION=OK\nTo open the management menu later, run: sudo mediahub")

    def register(self):
        path = str(self.root)
        if not re.fullmatch(r"/[A-Za-z0-9/_-]+", path):
            raise OperationError("Use an installation path without spaces or shell characters")
        private_write("/usr/local/bin/mediahub", "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
                      + f"export MEDIAHUB_DIR='{path}'\nexec python3 '{path}/scripts/mediahub.py' \"$@\"\n")
        Path("/usr/local/bin/mediahub").chmod(0o755)

    def install(self):
        self.clean()
        progress = self.state / "install.json"
        if read_json(progress, {}).get("phase") == "restoring":
            raise OperationError("Recovery is incomplete. Continue with Import backup and restore.")
        if (self.root / ".env").exists() and not progress.exists():
            raise OperationError("This server is already configured. Use Update or Telegram setup.")
        if read_json(progress, {}).get("phase") == "complete":
            self.register()
            print("Installation is already complete. Use the management menu.")
            return
        for directory in ("backups", "secrets"):
            (self.root / directory).mkdir(exist_ok=True, mode=0o700)
            (self.root / directory).chmod(0o700)
        if not (self.root / ".env").exists():
            # Do not generate a new database password against an existing volume.
            volumes = run(["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=mediahub-ai", "-q"], capture=True)
            if volumes:
                raise OperationError("Existing MediaHub volumes found: use Restore or recover the original .env")
            token = ask("Bot Token from BotFather", secret=True, pattern=r"[0-9]+:[A-Za-z0-9_-]{30,}")
            me = BotAPI(token).call("getMe")
            print("Selected bot: @" + me["username"])
            api_id = ask("Telegram API ID from my.telegram.org", pattern=r"[1-9][0-9]*")
            api_hash = ask("Telegram API Hash", secret=True, pattern=r"[a-fA-F0-9]{32}")
            admin = ask("Numeric Telegram user ID of the first superadmin", pattern=r"[1-9][0-9]*")
            timezone = ask("Timezone", "Asia/Tehran")
            ZoneInfo(timezone)
            password = secrets.token_hex(32)
            values = read_env(self.root / ".env.example")
            values.update(APP_ENV="production", DEBUG="false", TELEGRAM_BOT_TOKEN=token,
                          COMPOSE_PROJECT_NAME="mediahub-ai",
                          TELEGRAM_API_ID=api_id, TELEGRAM_API_HASH=api_hash,
                          TELEGRAM_SUPERADMIN_ID=admin, TELEGRAM_ADMIN_IDS="",
                          SECRET_KEY=secrets.token_hex(32), JWT_SECRET_KEY=secrets.token_hex(32),
                          BOT_BACKEND_API_KEY=secrets.token_hex(32),
                          DATA_ENCRYPTION_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                          POSTGRES_PASSWORD=password, DISPLAY_TIMEZONE=timezone, QUOTA_TIMEZONE=timezone,
                          DATABASE_URL=f"postgresql+asyncpg://mediahub:{password}@postgres:5432/mediahub")
            confirm("Install this bot and create a new configuration on this server?")
            save_json(progress, {"phase": "prepared"})
            write_env(self.root / ".env", values)
            private_write(self.state / "recovery-key.txt", values["DATA_ENCRYPTION_KEY"] + "\n")
        try:
            tag = self.git("describe", "--exact-match", "--tags", "HEAD")
        except OperationError:
            tag = None
        self.prepare(tag)
        # A new installation uses the Local Bot API. Cloud logout is required
        # by Telegram before migrating a token that was used on the public API.
        if read_json(progress, {}).get("phase") == "prepared":
            BotAPI(read_env(self.root / ".env")["TELEGRAM_BOT_TOKEN"]).call("logOut")
            save_json(progress, {"phase": "cloud_logged_out"})
        self.compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres", "redis", "telegram-api")
        self.activate()
        print("Keep a secure off-server copy of the recovery key, separate from your backups: " + str(self.state / "recovery-key.txt"))
        print("Start the bot in Telegram, then configure the reporting group.")
        if ask("Set up the reporting group and topics now? yes/no", "yes", pattern=r"yes|no") == "yes":
            self.telegram()

    def telegram(self):
        values = read_env(self.root / ".env")
        self.local({"action": "bootstrap"})
        current = self.local({"action": "routes"})
        api = LocalBotAPI(self)
        routes = configure_forum(values["TELEGRAM_BOT_TOKEN"], self.state / "telegram.json", current, api=api)
        self.local({"action": "forum", "routes": routes})
        print("TELEGRAM_TOPICS=OK; notification destinations saved to the database.")
        while ask("Add a required membership channel? yes/no", "no", pattern=r"yes|no") == "yes":
            self.local({"action": "channel", "channel": required_channel(values["TELEGRAM_BOT_TOKEN"], api=api)})
        print("Manage prices, payment cards, wallets, plans, and other settings in the bot admin panel.")

    def snapshot(self):
        images = {}
        services = (*APPS, YOUTUBE_PROVIDER) if self.has_youtube_provider() else APPS
        for name in services:
            info = json.loads(run(["docker", "inspect", "mediahub-" + name], capture=True))[0]
            if not info["State"]["Running"]:
                raise OperationError("Start all application services before updating")
            images[name] = info["Image"]
            run(["docker", "image", "tag", info["Image"], f"mediahub-ai-{name}:rollback-{int(time.time())}"])
        return {"head": self.git("rev-parse", "HEAD"), "images": images,
                "branch": self.git("branch", "--show-current")}

    def recover_application(self, previous, *, stopped):
        if stopped:
            self.compose("stop", *APPS)
        if self.has_youtube_provider() and YOUTUBE_PROVIDER not in previous["images"]:
            # The first helper installation must be reversible too. Compose
            # removes only its own service container, leaving data volumes alone.
            self.compose("rm", "--stop", "--force", YOUTUBE_PROVIDER)
        self.git("reset", "--keep", previous["head"])
        self.images_override(previous["images"])
        if stopped:
            self.start_apps()
        print("APPLICATION_ROLLBACK=OK DATABASE=NOT_DOWNGRADED")

    def update(self, version=None):
        self.clean()
        if (self.state / "update-pending.json").exists():
            raise OperationError("The previous update is incomplete. Run Roll back application first.")
        source = read_json(self.root / "secrets/update-source.json", {}).get("repository") or self.git("remote", "get-url", "origin")
        validate_source(source)
        if not version:
            available = self.git("ls-remote", "--tags", "--refs", source, "v*")
            versions = [line.rsplit("/", 1)[-1] for line in available.splitlines()]
            stable = [v for v in versions if re.fullmatch(r"v\d+\.\d+\.\d+", v)]
            if not stable:
                raise OperationError("No stable release tag has been published yet.")
            version = max(stable, key=lambda v: tuple(map(int, v[1:].split("."))))
        candidate = bool(re.fullmatch(r"[0-9a-f]{40}", version))
        if not candidate and not VERSION.fullmatch(version):
            raise OperationError("Enter an exact version, such as v1.0.0, or a full candidate commit.")
        self.git("fetch", source, version if candidate else "refs/tags/" + version)
        target = self.git("rev-parse", "FETCH_HEAD^{commit}")
        if candidate and target != version:
            raise OperationError("Candidate source differs from the requested commit")
        if target == self.git("rev-parse", "HEAD"):
            print("The selected version is already installed.")
            return
        self.git("merge-base", "--is-ancestor", "HEAD", target)
        manifest = json.loads(self.git("show", target + ":release.json"))
        if not candidate and manifest.get("version") != version:
            raise OperationError("Release metadata does not match its tag")
        confirm(f"Update to {version} ({target[:12]}) with a backup and restore verification?")
        self.idle()
        previous = self.snapshot()
        previous.update(installed_head=target, application_rollback_safe=manifest.get("application_rollback_safe") is True)
        stopped = bot_stopped = False
        save_json(self.state / "update-pending.json", previous)
        try:
            self.git("checkout", "--detach", target)
            self.prepare(None if candidate else version)
            self.idle()
            self.compose("stop", "bot")
            bot_stopped = True
            self.idle()
            stopped = True
            self.compose("stop", *APPS)
            backup = self.backup(verify=True)
            previous["backup_id"] = backup["id"]
            previous["application_rollback_safe"] = manifest.get("application_rollback_safe") is True
            save_json(self.state / "update-pending.json", previous)
            self.compose("run", "--rm", "--no-deps", "-T", "backend", "alembic", "upgrade", "head")
            self.start_apps()
            previous["installed_head"] = target
            save_json(self.state / "rollback.json", previous)
            (self.state / "update-pending.json").unlink()
            self.register()
            print("UPDATE=OK VERSION=" + version)
        except BaseException:
            if not stopped or manifest.get("application_rollback_safe") is True:
                self.recover_application(previous, stopped=stopped)
                if bot_stopped and not stopped:
                    self.compose("start", "bot")
                (self.state / "update-pending.json").unlink(missing_ok=True)
            else:
                print("UPDATE=FAILED; applications are stopped. Keep the backup and update-pending.json for recovery.")
            raise

    def rollback(self):
        pending = self.state / "update-pending.json"
        previous = read_json(pending) or read_json(self.state / "rollback.json")
        if not previous or not previous.get("application_rollback_safe"):
            raise OperationError("No compatible rollback version is recorded. Use backup recovery.")
        self.clean()
        if self.git("rev-parse", "HEAD") not in {previous["installed_head"], previous["head"] if pending.exists() else ""}:
            raise OperationError("Rollback belongs to a different installed version")
        confirm("Roll back the application to " + previous["head"][:12] + "? The database will not be downgraded.")
        self.idle()
        self.compose("stop", "bot")
        try:
            self.idle()
        except BaseException:
            self.compose("start", "bot")
            raise
        self.compose("stop", *APPS)
        try:
            self.backup(verify=True)
        except BaseException:
            if not pending.exists():
                self.start_apps()
            raise
        self.recover_application(previous, stopped=True)
        pending.unlink(missing_ok=True)

    def export(self):
        info = self.backup(verify=True)
        directory = Path(ask("Backup export directory (absolute path)"))
        if not directory.is_absolute():
            raise OperationError("An absolute destination is required")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for extension in (".mhb", ".json"):
            destination = directory / (info["id"] + extension)
            if destination.exists():
                raise OperationError("Destination already exists")
            shutil.copy2(self.root / "backups" / destination.name, destination)
            destination.chmod(0o600)
        print("BACKUP_EXPORTED=" + str(directory / (info["id"] + ".mhb")))
        print("Keep DATA_ENCRYPTION_KEY safe and separate from the exported backup files.")

    def import_backup(self):
        source = Path(ask("Absolute path to the .mhb backup file"))
        if not source.is_absolute() or not re.fullmatch(r"[a-f0-9]{32}\.mhb", source.name):
            raise OperationError("Invalid backup filename")
        metadata = source.with_suffix(".json")
        if not source.is_file() or not metadata.is_file():
            raise OperationError("Both .mhb and matching .json are required")
        target_dir = self.root / "backups"
        target_dir.mkdir(mode=0o700, exist_ok=True)
        resume = read_json(self.state / "install.json", {})
        restoring = resume.get("phase") == "restoring" and resume.get("backup_id") == source.stem
        for file in (source, metadata):
            target = target_dir / file.name
            if file.resolve() != target.resolve():
                if target.exists():
                    if file_checksum(file) == file_checksum(target):
                        continue
                    raise OperationError("A different backup with this ID already exists on the destination")
                shutil.copy2(file, target)
                target.chmod(0o600)
        if (self.root / ".env").exists() and not restoring:
            # Existing host: database-only recovery retains the current environment and encryption key.
            run(["bash", self.root / "scripts/restore.sh", source.stem], cwd=self.root,
                env={**os.environ, "MEDIAHUB_DIR": str(self.root)})
            return
        self.clean()
        if restoring and ((self.state / "recovery-config").exists() or (self.root / ".env").exists()):
            self.finish_import(source.stem)
            return
        volumes = run(["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=mediahub-ai", "-q"], capture=True)
        if volumes:
            raise OperationError("Existing MediaHub volumes require the original .env; fresh restore refused")
        confirm("Restore on a new server. The bot on the old server must be stopped.", "OLD-SERVER-STOPPED")
        key = ask("Backup DATA_ENCRYPTION_KEY", secret=True, pattern=r"[A-Za-z0-9_-]{43}=")
        image = "mediahub-backup:recovery"
        run(["docker", "build", "-f", "backup/Dockerfile", "-t", image, "."], cwd=self.root)
        with tempfile.TemporaryDirectory(prefix="recovery-", dir=self.state) as directory:
            runtime_env = {**os.environ, "DATA_ENCRYPTION_KEY": key,
                           "POSTGRES_DB": "mediahub", "POSTGRES_USER": "mediahub", "POSTGRES_PASSWORD": "unused"}
            extracted = json.loads(run(["docker", "run", "--rm", "--network", "none", "--env", "DATA_ENCRYPTION_KEY",
                 "--env", "POSTGRES_DB", "--env", "POSTGRES_USER", "--env", "POSTGRES_PASSWORD",
                 "--mount", f"type=bind,src={target_dir},dst=/backups,readonly",
                 "--mount", f"type=bind,src={directory},dst=/recovery", image,
                 "extract-config", source.stem, "--output", "/recovery/config"], env=runtime_env, capture=True))
            schema = extracted.get("schema_version")
            if schema and not any((self.root / "backend/alembic/versions").glob(schema + "_*.py")):
                raise OperationError("This code version does not recognize the backup database schema. Download a compatible release first.")
            config = Path(directory) / "config"
            restored = read_env(config / "mediahub.env")
            if restored.get("DATA_ENCRYPTION_KEY") != key:
                raise OperationError("Backup key and restored application key differ")
            if restored.get("POSTGRES_HOST", "postgres") != "postgres":
                raise OperationError("This restore wizard requires the dedicated Compose PostgreSQL")
            db_url = urlsplit(restored.get("DATABASE_URL", ""))
            redis_url = urlsplit(restored.get("REDIS_URL", ""))
            if db_url.hostname != "postgres" or redis_url.hostname != "redis" or redis_url.path not in ("", "/", "/0"):
                raise OperationError("Restore requires dedicated Compose PostgreSQL and Redis DB 0")
            if (self.root / "secrets").exists() and any((self.root / "secrets").iterdir()):
                raise OperationError("Destination secrets directory is not empty")
            staging = self.state / "recovery-config"
            if staging.exists():
                raise OperationError("A previous recovery configuration needs inspection")
            save_json(self.state / "install.json", {"phase": "restoring", "backup_id": source.stem})
            shutil.move(str(config), staging)
        self.finish_import(source.stem)

    def finish_import(self, backup_id):
        progress_path = self.state / "install.json"
        progress = read_json(progress_path, {})
        staging = self.state / "recovery-config"
        if not (self.root / ".env").exists():
            restored = read_env(staging / "mediahub.env")
            if (staging / "secrets").exists():
                shutil.copytree(staging / "secrets", self.root / "secrets", dirs_exist_ok=True)
            else:
                (self.root / "secrets").mkdir(mode=0o700, exist_ok=True)
            write_env(self.root / ".env", {**restored, "COMPOSE_PROJECT_NAME": "mediahub-ai"})
        restored = read_env(self.root / ".env")
        try:
            tag = self.git("describe", "--exact-match", "--tags", "HEAD")
        except OperationError:
            tag = None
        self.prepare(tag)
        self.compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres", "redis", "telegram-api")
        if progress.get("reconciled"):
            self.idle()
        self.compose("stop", *APPS)
        if not progress.get("database_restored"):
            self.compose("run", "--rm", "--no-deps", "-T", "backup", "restore", backup_id,
                         "--confirm", "RESTORE:" + restored["POSTGRES_DB"])
            progress["database_restored"] = True
            save_json(progress_path, progress)
        self.compose("run", "--rm", "--no-deps", "-T", "backend", "alembic", "upgrade", "head")
        if not progress.get("reconciled"):
            self.local({"action": "reconcile_restore"})
            self.compose("exec", "-T", "redis", "redis-cli", "FLUSHDB")
            progress["reconciled"] = True
            save_json(progress_path, progress)
        self.activate(bootstrap=False)
        if staging.exists():
            shutil.rmtree(staging)
        print("RESTORE_NEW_SERVER=OK")

    def source(self):
        repository = ask("Update repository HTTPS URL", self.git("remote", "get-url", "origin"))
        validate_source(repository)
        registry = ask("Container image registry path", "ghcr.io/ataamini-bot/mediahub-ai")
        if not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9/_-]+", registry):
            raise OperationError("Invalid registry")
        confirm("Trust this repository and registry as the source for future application updates?")
        save_json(self.root / "secrets/update-source.json", {"repository": repository, "registry": registry})

    def uninstall(self):
        confirm("Stop and remove MediaHub containers while preserving data and backups?", "UNINSTALL")
        self.idle()
        self.compose("stop", "bot")
        try:
            self.idle()
            self.backup(verify=True)
        except BaseException:
            self.compose("start", "bot")
            raise
        self.compose("down")  # Never --volumes: database removal is not an uninstall default.
        print("UNINSTALL=OK DATA=PRESERVED")

    def menu(self):
        options = {"1": ("Install MediaHub", self.install), "2": ("Update to a stable release", self.update),
                   "3": ("Create and export backup", self.export), "4": ("Import backup and restore", self.import_backup),
                   "5": ("Telegram group, topics, and required channels", self.telegram),
                   "6": ("Check system health", lambda: run(["bash", "scripts/check_launch.sh"], cwd=self.root)),
                   "7": ("Roll back application", self.rollback), "8": ("Configure update source", self.source),
                   "9": ("Uninstall containers (keep data)", self.uninstall)}
        while True:
            print("\nMediaHub AI - Server Management")
            for key, (label, _) in options.items():
                print(f"{key}. {label}")
            print("0. Exit")
            key = ask("Select an option", pattern=r"[0-9]")
            if key == "0":
                return
            try:
                options[key][1]()
            except (OperationError, ValueError, OSError) as exc:
                print("ERROR: " + (str(exc) if isinstance(exc, OperationError) else type(exc).__name__))


def file_checksum(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class LocalBotAPI:
    def __init__(self, console):
        self.console = console

    def call(self, method, **payload):
        result = self.console.local({"action": "telegram_api", "method": method, "parameters": payload})
        if not result.get("ok"):
            raise OperationError("Telegram " + method + ": " + str(result.get("description", "request failed"))[:200])
        return result["result"]


def validate_source(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OperationError("Enter an HTTPS URL without a token or password. Use a credential helper for authentication.")


def main():
    parser = argparse.ArgumentParser(description="MediaHub server operations")
    parser.add_argument("action", nargs="?", default="menu", choices=["menu", "install", "update", "telegram", "backup", "export", "import", "rollback", "source", "uninstall", "register"])
    parser.add_argument("version", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise OperationError("Run this command with sudo.")
    if not sys.stdin.isatty() and args.action not in {"backup", "register"}:
        raise OperationError("Interactive terminal required; connect the command to /dev/tty")
    os.umask(0o077)
    console = Console()
    with (console.state / "operations.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OperationError("Another installation or recovery operation is running.") from None
        if args.action == "update":
            console.update(args.version)
        elif args.action == "import":
            console.import_backup()
        else:
            getattr(console, args.action)()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOPERATION=INTERRUPTED; check the service status.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print("OPERATION=FAILED " + (str(exc) if isinstance(exc, OperationError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
