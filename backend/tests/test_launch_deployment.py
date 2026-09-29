"""Run the deployment shell with isolated Git/Docker commands, never a server."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


BASE = "c8a94d6dd84b145f1666091dcef5bf31bba4fd3e"
PREPARED = "f6de11031c88b9848f5ffcb806eb686fb99e1793"
LAUNCHED = "7d9dd72b85bd74086c6b80ef6c1300a9f86832ae"
CHANNEL_LANGUAGES = "f52fb5be34a46ecc4c6d5a3a66fd0b4f86d6c928"
PRIVATE_PAYMENTS = "506c5989a138785a16a55cc08fdb9588c836b7ca"
BROADCASTS = "0322acc644817859c737abbd165421218a488ab8"
TARGET = "a" * 40
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/deploy_launch_operations.sh"
SERVICES = ("backend", "bot", "worker", "monitor")

# Only external commands are replaced. Bash executes the complete production
# script, including its preconditions, ordering and EXIT rollback handler.
COMMAND = r'''
import json, os, sys
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
root = Path(os.environ["MEDIAHUB_DIR"])
path = root / "state.json"
state = json.loads(path.read_text())
state["calls"].append([name, *args])
def done(output="", code=0):
    path.write_text(json.dumps(state))
    if output:
        print(output)
    raise SystemExit(code)
if name in ("curl", "sleep"):
    done()
if name == "git":
    if args == ["branch", "--show-current"]:
        done(state.get("branch", "feature/admin-foundation"))
    if args == ["status", "--porcelain"]:
        done(state.get("dirty", ""))
    if args == ["rev-parse", "HEAD"]:
        done(state["head"])
    if args == ["rev-parse", "FETCH_HEAD"]:
        done(state["target"])
    if args[:2] == ["fetch", "origin"] or args[:2] == ["merge-base", "--is-ancestor"]:
        done()
    if args[:2] in (["merge", "--ff-only"], ["reset", "--keep"]):
        state["head"] = args[2]
        done()
if name == "docker":
    if args[0] == "inspect":
        service = args[-1].removeprefix("mediahub-")
        image = "sha256:" + str(["backend", "bot", "worker", "monitor", "backup"].index(service) + 1) * 64
        if args[2] == "{{.State.Running}}":
            running = service != "backup" or state.get("backup_running")
            done("true" if running else "false")
        if args[2] in ("{{.Image}}", "{{.Config.Image}}"):
            done(image)
        if args[2] == "{{.State.Status}}":
            done("running")
        if args[2] == "{{.RestartCount}}":
            done("0")
    if args[:2] == ["image", "tag"]:
        if state.get("fail_restore_tag") and args[-1] == "mediahub-ai-backend":
            done(code=1)
        done(code=1 if args[-1].startswith("sha256:") else 0)
    if args[:4] == ["compose", "config", "--format", "json"]:
        done(json.dumps({"name": "mediahub-ai", "services": {
            service: {"build": {"context": "."}}
            for service in ("backend", "bot", "worker", "monitor", "backup")
        }}))
    if args[:2] == ["compose", "exec"] and "postgres" in args:
        state["job_checks"] = state.get("job_checks", 0) + 1
        done("1" if state.get("busy_after_stop") and state["job_checks"] == 3 else "0")
    if args[:2] == ["compose", "exec"] and "backup" in args:
        done()
    if args[:2] == ["compose", "build"]:
        done(code=1 if state.get("fail") == "build" else 0)
    if args[:2] == ["compose", "run"]:
        if "backup" in args:
            operation = args[args.index("backup") + 1]
            if state.get("fail") == operation:
                done("BACKUP_ERROR=TestFailure", code=1)
            if operation in ("create", "verify"):
                done(json.dumps({"id": "b" * 32}))
        if "alembic" in args:
            done(code=1 if state.get("fail") == "migration" else 0)
        if "python" in args:
            done()
    if args[:2] in (["compose", "stop"], ["compose", "up"], ["compose", "start"]):
        done()
done("Unexpected stub command: " + repr([name, *args]), code=97)
'''


def deploy(tmp_path, **scenario):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    command = command_dir / "command"
    command.write_text(f"#!{sys.executable}\n" + COMMAND)
    command.chmod(0o700)
    for name in ("git", "docker", "curl", "sleep"):
        (command_dir / name).symlink_to(command)
    (tmp_path / ".env").write_text("TEST_DEPLOYMENT=true\n")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"head": PREPARED, "target": TARGET, "calls": [], **scenario}))
    result = subprocess.run(
        ["bash", str(SCRIPT), TARGET],
        env={**os.environ, "MEDIAHUB_DIR": str(tmp_path),
             "PATH": str(command_dir) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True, timeout=20,
    )
    return result, json.loads(state_path.read_text())


@pytest.mark.parametrize("head", [BASE, PREPARED, LAUNCHED, CHANNEL_LANGUAGES, PRIVATE_PAYMENTS, BROADCASTS, TARGET])
def test_clean_prepared_checkout_still_builds_and_backs_up_before_migration(tmp_path, head):
    result, state = deploy(tmp_path, head=head)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOYMENT=OK" in result.stdout
    assert state["head"] == TARGET
    calls = state["calls"]
    build = next(i for i, c in enumerate(calls) if c[:3] == ["docker", "compose", "build"])
    create = next(i for i, c in enumerate(calls) if c[-2:] == ["backup", "create"])
    verify = next(i for i, c in enumerate(calls) if c[-3:-1] == ["backup", "verify"])
    migrate = next(i for i, c in enumerate(calls) if "alembic" in c)
    assert build < create < verify < migrate
    assert not any(c[:2] == ["git", "reset"] for c in calls)


@pytest.mark.parametrize("scenario, marker", [
    ({"dirty": " M docker-compose.yml"}, "ABORTED_LOCAL_CHANGES"),
    ({"branch": "develop"}, "ABORTED_UNEXPECTED_BRANCH"),
    ({"head": "c" * 40}, "ABORTED_UNEXPECTED_HEAD"),
])
def test_unexpected_checkout_aborts_without_touching_docker(tmp_path, scenario, marker):
    result, state = deploy(tmp_path, **scenario)
    assert result.returncode != 0
    assert marker in result.stdout
    assert not any(c[0] == "docker" for c in state["calls"])
    assert not any(c[:2] == ["git", "reset"] for c in state["calls"])


@pytest.mark.parametrize("failure", ["create", "verify", "migration"])
def test_failed_install_restores_original_head_and_exact_running_images(tmp_path, failure):
    result, state = deploy(tmp_path, fail=failure)
    assert result.returncode != 0
    assert "ROLLBACK=ATTEMPTED" in result.stdout
    assert "DEPLOYMENT=OK" not in result.stdout
    assert state["head"] == PREPARED
    calls = state["calls"]
    for index, service in enumerate(SERVICES, 1):
        assert ["docker", "image", "tag", "sha256:" + str(index) * 64,
                "mediahub-ai-" + service] in calls
    assert not any(c[:3] == ["docker", "image", "tag"] and c[-1].startswith("sha256:") for c in calls)
    restart = next(c for c in calls if c[:3] == ["docker", "compose", "up"])
    assert "--no-build" in restart and "never" in restart
    assert set(SERVICES) <= set(restart)
    if failure != "migration":
        assert not any("alembic" in c for c in calls)


def test_job_arriving_after_bot_stop_does_not_restart_active_worker(tmp_path):
    result, state = deploy(tmp_path, busy_after_stop=True)
    assert result.returncode != 0
    assert state["head"] == PREPARED
    restarts = [c for c in state["calls"] if c[:3] in
                (["docker", "compose", "up"], ["docker", "compose", "start"])]
    assert restarts == [["docker", "compose", "start", "bot"]]
    assert not any("alembic" in c for c in state["calls"])


def test_build_failure_keeps_existing_services_running(tmp_path):
    result, state = deploy(tmp_path, fail="build", backup_running=True)
    assert result.returncode != 0
    assert state["head"] == PREPARED
    assert not any(c[:3] in (["docker", "compose", "stop"], ["docker", "compose", "up"])
                   for c in state["calls"])


def test_failed_image_recovery_does_not_restart_with_wrong_images(tmp_path):
    result, state = deploy(tmp_path, fail="verify", fail_restore_tag=True)
    assert result.returncode != 0
    assert "ROLLBACK=INCOMPLETE_MANUAL_RECOVERY_REQUIRED" in result.stdout
    assert state["head"] == PREPARED
    assert not any(c[:3] == ["docker", "compose", "up"] for c in state["calls"])
