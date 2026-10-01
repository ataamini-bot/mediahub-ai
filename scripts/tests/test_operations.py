import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mediahub
from ops_common import OperationError, read_env, read_json, save_json, write_env
from telegram_setup import ensure_topics


class FakeTelegram:
    def __init__(self, fail=None, forum=True, rights=True):
        self.fail, self.forum, self.rights = fail, forum, rights
        self.created = []

    def call(self, method, **payload):
        if method == "getMe":
            return {"id": 77}
        if method == "getChat":
            return {"id": -100999, "type": "supergroup", "is_forum": self.forum}
        if method == "getChatMember":
            return {"status": "administrator", "can_manage_topics": self.rights}
        self.created.append(payload["name"])
        if payload["name"] == self.fail:
            raise OperationError("reply lost")
        return {"message_thread_id": 100 + len(self.created)}


def test_topics_created_once_and_resume_uses_persisted_ids(tmp_path):
    api = FakeTelegram()
    journal = tmp_path / "journal.json"
    first = ensure_topics(api, -100999, journal)
    second = ensure_topics(api, -100999, journal)
    assert first == second
    assert api.created == ["Monitoring", "Payments", "Backups", "System", "Support"]
    assert journal.stat().st_mode & 0o777 == 0o600


def test_lost_reply_never_automatically_recreates_topic(tmp_path):
    api = FakeTelegram(fail="Payments")
    journal = tmp_path / "journal.json"
    with pytest.raises(OperationError):
        ensure_topics(api, -100999, journal)
    api.fail = None
    with pytest.raises(OperationError, match="Payments"):
        ensure_topics(api, -100999, journal)
    assert api.created == ["Monitoring", "Payments"]
    result = ensure_topics(api, -100999, journal, recover=lambda title: 555)
    assert result["topics"]["payments"] == 555
    assert api.created.count("Payments") == 1


@pytest.mark.parametrize("forum,rights", [(False, True), (True, False)])
def test_wrong_group_or_rights_fail_before_creating_any_topic(tmp_path, forum, rights):
    api = FakeTelegram(forum=forum, rights=rights)
    with pytest.raises(OperationError):
        ensure_topics(api, -100999, tmp_path / "journal", recover=lambda _: None)
    assert api.created == []


def test_existing_database_destinations_are_reused(tmp_path):
    api = FakeTelegram()
    existing = {"monitoring": 2, "payments": 4, "backups": 6, "system": 8}
    result = ensure_topics(api, -100999, tmp_path / "journal", existing)
    assert api.created == ["Support"]
    assert {k: result["topics"][k] for k in existing} == existing


def test_panel_topic_change_wins_over_old_installation_journal(tmp_path):
    api = FakeTelegram()
    journal = tmp_path / "journal"
    ensure_topics(api, -100999, journal)
    result = ensure_topics(api, -100999, journal, {"payments": 999})
    assert result["topics"]["payments"] == 999
    assert len(api.created) == 5


def test_environment_round_trip_is_literal_and_private(tmp_path, monkeypatch):
    monkeypatch.setenv("DANGER", "expanded")
    path = tmp_path / ".env"
    values = {"PASSWORD": "$(touch /tmp/never) ${DANGER} `echo no` ' \\ #", "API_HASH": "a" * 32}
    write_env(path, values)
    assert read_env(path) == values
    assert path.stat().st_mode & 0o777 == 0o600
    write_env(path, {"APP_ENV": "production"})
    assert read_env(path)["PASSWORD"] == values["PASSWORD"]
    with pytest.raises(OperationError):
        write_env(path, {"BAD": "one\ntwo"})
    assert read_env(path)["PASSWORD"] == values["PASSWORD"]


@pytest.mark.parametrize("source", ["http://host/repo.git", "https://token@host/repo", "https://host/repo?token=x", "file:///tmp/repo", "--upload-pack=evil"])
def test_update_source_rejects_insecure_or_credential_urls(source):
    with pytest.raises(OperationError):
        mediahub.validate_source(source)


class UpdateHarness(mediahub.Console):
    def __init__(self, root, fail=None):
        super().__init__(root)
        self.fail = fail
        self.calls = []
        self.head = "a" * 40
        self.idle_checks = 0

    def clean(self):
        pass

    def git(self, *args):
        self.calls.append(("git", *args))
        if args == ("remote", "get-url", "origin"):
            return "https://github.com/example/repo.git"
        if args == ("rev-parse", "FETCH_HEAD^{commit}"):
            return "b" * 40
        if args == ("rev-parse", "HEAD"):
            return self.head
        if args[0] == "show":
            return json.dumps({"version": "v1.0.1", "application_rollback_safe": True})
        if args[:2] in [("checkout", "--detach"), ("reset", "--keep")]:
            self.head = args[2]
        return ""

    def snapshot(self):
        return {"head": self.head, "images": {name: "sha256:" + name for name in mediahub.APPS}}

    def idle(self):
        self.idle_checks += 1
        if self.fail == "busy" and self.idle_checks == 3:
            raise OperationError("active download")

    def prepare(self, version=None):
        self.calls.append(("prepare", version))
        if self.fail == "pull":
            raise OperationError("registry unreachable")

    def compose(self, *args, **kwargs):
        self.calls.append(("compose", *args))
        if "alembic" in args and self.fail == "migration":
            raise OperationError("migration failed")

    def backup(self, verify=False):
        self.calls.append(("backup", verify))
        if self.fail == "backup":
            raise OperationError("snapshot failed")
        return {"id": "d" * 32}

    def start_apps(self):
        self.calls.append(("start_apps", self.head))
        if self.fail == "health" and self.head == "b" * 40:
            raise OperationError("health failed")

    def register(self):
        self.calls.append(("register",))


@pytest.mark.parametrize("failure", ["pull", "busy", "backup", "migration", "health"])
def test_update_failure_restores_actual_images_and_original_revision(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(mediahub, "confirm", lambda *args: None)
    console = UpdateHarness(tmp_path, failure)
    with pytest.raises(OperationError):
        console.update("v1.0.1")
    assert console.head == "a" * 40
    override = read_json(tmp_path / "docker-compose.override.yml")
    assert override["services"]["backend"]["image"] == "sha256:backend"
    assert not (console.state / "update-pending.json").exists()
    stops = [c for c in console.calls if c[:2] == ("compose", "stop")]
    if failure == "pull":
        assert not stops and not any(c[0] == "start_apps" for c in console.calls)
    elif failure == "busy":
        assert stops == [("compose", "stop", "bot")]
        assert ("compose", "start", "bot") in console.calls
    else:
        assert ("start_apps", "a" * 40) in console.calls


def test_update_orders_verified_backup_before_migration_and_saves_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(mediahub, "confirm", lambda *args: None)
    console = UpdateHarness(tmp_path)
    console.update("v1.0.1")
    backup_index = console.calls.index(("backup", True))
    migration_index = next(i for i, c in enumerate(console.calls) if "alembic" in c)
    assert backup_index < migration_index
    assert console.head == "b" * 40
    assert read_json(console.state / "rollback.json")["backup_id"] == "d" * 32


def test_interrupted_update_blocks_another_update(tmp_path):
    console = UpdateHarness(tmp_path)
    save_json(console.state / "update-pending.json", {"head": "a" * 40})
    with pytest.raises(OperationError, match="incomplete"):
        console.update("v1.0.1")
    assert not console.calls


def test_registry_image_revision_mismatch_never_changes_compose(tmp_path, monkeypatch):
    console = mediahub.Console(tmp_path)
    monkeypatch.setattr(console, "git", lambda *args: "a" * 40)
    def command(args, **kwargs):
        return json.dumps([{"Config": {"Labels": {"org.opencontainers.image.revision": "wrong"}}}]) if args[1:3] == ["image", "inspect"] else ""
    monkeypatch.setattr(mediahub, "run", command)
    with pytest.raises(OperationError, match="revision"):
        console.prepare("v1.0.0")
    assert not (tmp_path / "docker-compose.override.yml").exists()


def test_resume_restore_after_app_failure_never_replays_snapshot_or_queues(tmp_path, monkeypatch):
    console = UpdateHarness(tmp_path)
    write_env(tmp_path / ".env", {"POSTGRES_DB": "mediahub"})
    save_json(console.state / "install.json", {"phase": "restoring", "backup_id": "d" * 32})
    calls = []
    monkeypatch.setattr(console, "local", lambda payload: calls.append(payload["action"]))
    def fail_apps(**kwargs):
        raise OperationError("application failed after database recovery")
    monkeypatch.setattr(console, "activate", fail_apps)
    with pytest.raises(OperationError):
        console.finish_import("d" * 32)
    assert read_json(console.state / "install.json")["reconciled"]
    monkeypatch.setattr(console, "activate", lambda **kwargs: None)
    console.finish_import("d" * 32)
    assert len([c for c in console.calls if "restore" in c]) == 1
    assert calls == ["reconcile_restore"]
    assert len([c for c in console.calls if "FLUSHDB" in c]) == 1
