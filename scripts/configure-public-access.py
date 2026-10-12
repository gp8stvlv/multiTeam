#!/usr/bin/env python3
"""Create the shared team credential outside Git; do not reload or deploy Nginx."""
import argparse
import fcntl
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

KEY_PATTERN = re.compile(r"gg_team_[A-Za-z0-9_-]{43}\Z")
REPOSITORY = Path(__file__).resolve().parents[1]


def private_directory(value: str) -> Path:
    directory = Path(value).expanduser().resolve()
    if directory == REPOSITORY or REPOSITORY in directory.parents:
        raise ValueError("Public access credentials must be stored outside the repository")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def write_atomic(path: Path, value: str, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".public-access-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(directory: Path, rotate: bool = False) -> str:
    key_file = directory / "team-key"
    if key_file.exists() and not rotate:
        key = key_file.read_text(encoding="ascii").strip()
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError("Stored team key is invalid; use --rotate explicitly to replace it")
    else:
        key = "gg_team_" + secrets.token_urlsafe(32)

    # The credential is passed only via stdin, never through process arguments.
    hashed = subprocess.run(
        ["openssl", "passwd", "-apr1", "-stdin"], input=key + "\n",
        text=True, capture_output=True, check=False,
    )
    if hashed.returncode != 0 or not hashed.stdout.strip().startswith("$apr1$"):
        raise RuntimeError("OpenSSL could not create the browser password hash")

    nginx_directory = directory / "nginx"
    nginx_directory.mkdir(mode=0o755, exist_ok=True)
    nginx_directory.chmod(0o755)
    # The 0700 host parent protects all files. Inside the container, the child
    # mount is traversable so unprivileged workers can read only the hash file.
    write_atomic(nginx_directory / "team-api-key.map", f"~^{key}$ 1;\n", 0o600)
    write_atomic(nginx_directory / "docs.htpasswd", "team:" + hashed.stdout.strip() + "\n", 0o644)
    write_atomic(key_file, key + "\n", 0o600)
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=os.getenv("PUBLIC_ACCESS_DIRECTORY", "/var/lib/gastrogid/access"))
    parser.add_argument("--rotate", action="store_true", help="Replace the existing team key; reload the public gateway afterward")
    parser.add_argument("--show", action="store_true", help="Explicitly print the team key; otherwise no credential is printed")
    args = parser.parse_args()
    try:
        directory = private_directory(args.directory)
        with (directory / ".configure.lock").open("a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            key = configure(directory, rotate=args.rotate)
        if args.show:
            print(key)
        else:
            print("Team API access configured; credential remains outside the repository.")
    except (OSError, ValueError, RuntimeError) as exc:
        # Errors intentionally never include key content or child-process stderr.
        print(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Could not configure public access files", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
