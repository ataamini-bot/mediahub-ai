import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_release_images import verify


@pytest.mark.parametrize("problem", [None, "private", "wrong-revision", "wrong-version", "wrong-platform", "unrelated-digest", "wrong-checkout"])
def test_operator_can_only_pass_with_three_public_images_of_the_tag(tmp_path, problem, monkeypatch):
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "must-not-reach-docker")
    sha, tag = "a" * 40, "v1.0.0"
    (tmp_path / "release.json").write_text(json.dumps({"version": tag}))
    pulls = []

    def run(command, **kwargs):
        if command[0] == "git":
            return SimpleNamespace(stdout=("b"*40 if problem == "wrong-checkout" and command[-1] == "HEAD" else sha))
        assert command[0:2] == ["docker", "--config"]
        assert "DOCKER_AUTH_CONFIG" not in kwargs["env"]
        assert json.loads((Path(command[2]) / "config.json").read_text()) == {"auths": {"ghcr.io": {}}}
        image = command[-1].split(":")[0]
        repo = "ghcr.io/example/mediahub-ai/"
        name = image.removeprefix(repo)
        assert command[-1] == repo + name + ":" + tag
        if command[3] == "pull":
            assert command[4:6] == ["--platform", "linux/amd64"]
            pulls.append(name)
            if problem == "private" and name == "backup":
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(stdout="")
        assert command[3:5] == ["image", "inspect"] and pulls[-1] == name
        current = problem if name == "backup" else None
        labels = {"org.opencontainers.image.revision": "b"*40 if current == "wrong-revision" else sha,
                  "org.opencontainers.image.version": "latest" if current == "wrong-version" else tag}
        return SimpleNamespace(stdout=json.dumps([{"Config": {"Labels": labels}, "Os": "linux",
            "Architecture": "arm64" if current == "wrong-platform" else "amd64",
            "RepoDigests": [(repo + "other" if current == "unrelated-digest" else image) + "@sha256:" + "9"*64]}]))

    if problem:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            verify(tag, "Example/MediaHub-AI", tmp_path, run)
        if problem == "wrong-checkout":
            assert not pulls
    else:
        digests = verify(tag, "Example/MediaHub-AI", tmp_path, run)
        assert pulls == ["backend", "bot", "backup"] and len(set(digests)) == 3
