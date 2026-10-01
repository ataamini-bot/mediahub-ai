"""Host-side operations helpers. Secrets are never command-line arguments."""
import getpass
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


class OperationError(Exception):
    pass


def private_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise OperationError("Refusing to replace a symbolic link")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mediahub-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_json(path, data):
    private_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def read_env(path):
    from dotenv import dotenv_values
    # No shell evaluation and no interpolation of host environment variables.
    return dict(dotenv_values(path, interpolate=False))


def write_env(path, changes):
    from dotenv import dotenv_values
    values = dict(dotenv_values(path, interpolate=False)) if Path(path).exists() else {}
    values.update(changes)
    lines = []
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise OperationError("Invalid environment key")
        value = str(value or "")
        if any(c in value for c in "\n\r\x00"):
            raise OperationError("Multiline environment values are not supported")
        # Compose/dotenv single-quoted values are literal (including $).
        lines.append(key + "='" + value.replace("\\", "\\\\").replace("'", "\\'") + "'")
    private_write(path, "\n".join(lines) + "\n")


def ask(label, default=None, *, secret=False, pattern=None):
    while True:
        prompt = label + (f" [{default}]" if default is not None and not secret else "") + ": "
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if not value and default is not None:
            value = str(default)
        if value and (pattern is None or re.fullmatch(pattern, value)):
            return value
        print("Invalid value. Please try again.")


def confirm(label, token="YES"):
    if input(f"{label}\nType {token} to continue: ").strip() != token:
        raise OperationError("Operation cancelled: confirmation was not provided.")


def run(args, *, cwd=None, capture=False, data=None, env=None, timeout=None):
    result = subprocess.run(list(map(str, args)), cwd=cwd, text=True, input=data,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None,
                            env=env, timeout=timeout)
    if result.returncode:
        # argv/output may contain a registry URL or diagnostics with secrets.
        raise OperationError(f"Command failed: {Path(str(args[0])).name} (exit {result.returncode})")
    return result.stdout.strip() if capture else ""


class BotAPI:
    def __init__(self, token):
        self.token = token

    def call(self, method, **payload):
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + self.token + "/" + method,
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                result = json.load(exc)
            except (ValueError, OSError):
                raise OperationError(f"Telegram {method}: HTTP {exc.code}") from None
        except (OSError, ValueError):
            raise OperationError(f"Telegram {method}: connection failed; check the result in Telegram before retrying.") from None
        if not result.get("ok"):
            detail = str(result.get("description", "request failed")).replace(self.token, "[hidden]")[:180]
            raise OperationError(f"Telegram {method}: {detail}")
        return result["result"]


TOPICS = {"monitoring": "Monitoring", "payments": "Payments", "backups": "Backups",
          "system": "System", "support": "Support"}
