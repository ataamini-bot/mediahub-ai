"""Check what an unauthenticated Linux/amd64 installation can actually pull."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

COMPONENTS = ("backend", "bot", "backup")


def verify(tag, repository, root, run=subprocess.run):
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-rc\.\d+)?", tag):
        raise ValueError("Expected a version tag such as v1.0.0")
    repository = repository.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*", repository):
        raise ValueError("Expected a GitHub owner/repository")

    def output(*args):
        return run(list(args), cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    commit = output("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if output("git", "rev-parse", "HEAD") != commit or json.loads((root / "release.json").read_text())["version"] != tag:
        raise ValueError("Check out the exact tag with matching release.json before verifying")
    verified = []
    with TemporaryDirectory(prefix="mediahub-anonymous-docker-") as private:
        # No docker/login-action in this job. A nonempty auths map containing an
        # empty entry also prevents auto-discovery of a host credential store.
        Path(private, "config.json").write_text('{"auths":{"ghcr.io":{}}}\n')
        docker = ["docker", "--config", private]
        for component in COMPONENTS:
            image = f"ghcr.io/{repository}/{component}"
            ref = f"{image}:{tag}"
            run([*docker, "pull", "--platform", "linux/amd64", ref], cwd=root, check=True)
            item, = json.loads(output(*docker, "image", "inspect", ref))
            labels = item.get("Config", {}).get("Labels") or {}
            if (item.get("Os"), item.get("Architecture")) != ("linux", "amd64"):
                raise ValueError(f"Wrong platform: {ref}")
            if labels.get("org.opencontainers.image.revision") != commit or labels.get("org.opencontainers.image.version") != tag:
                raise ValueError(f"Image labels do not match the tagged source: {ref}")
            digest = next((d for d in (item.get("RepoDigests") or []) if re.fullmatch(re.escape(image) + r"@sha256:[0-9a-f]{64}", d)), None)
            if not digest:
                raise ValueError(f"No matching registry digest: {ref}")
            verified.append(digest)
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", "ataamini-bot/mediahub-ai"))
    args = parser.parse_args()
    try:
        digests = verify(args.tag, args.repository, Path(__file__).resolve().parents[1])
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"PUBLIC_IMAGES=FAILED: {exc}\n")
    for digest in digests:
        print(digest)
    print("PUBLIC_IMAGES=OK")


if __name__ == "__main__":
    main()
