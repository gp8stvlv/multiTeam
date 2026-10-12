"""Generate a private .env from the documented template without overwriting it."""
import os
from pathlib import Path
import secrets


def configure(directory: Path) -> Path:
    target = directory / ".env"
    generated = {key: secrets.token_hex(32) for key in (
        "POSTGRES_PASSWORD", "AUTH_SECRET", "INTERNAL_TOKEN", "DEMO_ADMIN_PASSWORD"
    )}
    # An example API key must never be treated as a configured LLM provider.
    generated["LLM_API_KEY"] = ""
    lines = []
    for line in (directory / ".env.example").read_text().splitlines():
        key = line.partition("=")[0]
        lines.append(f"{key}={generated[key]}" if key in generated else line)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("\n".join(lines) + "\n")
    return target


if __name__ == "__main__":
    try:
        configure(Path(__file__).resolve().parents[1])
    except FileExistsError:
        raise SystemExit(".env already exists; edit it instead of replacing database secrets")
    print("Created private .env. Set LLM_API_KEY to enable live recommendations.")
