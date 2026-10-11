"""Create local/server configuration without printing secrets or overwriting it."""
from pathlib import Path
import secrets

target = Path(__file__).resolve().parents[1] / ".env"
if target.exists():
    raise SystemExit(".env already exists; edit it instead of replacing database secrets")
template = (target.parent / ".env.example").read_text()
for key in ("POSTGRES_PASSWORD", "AUTH_SECRET", "INTERNAL_TOKEN", "DEMO_ADMIN_PASSWORD"):
    template = template.replace(key + "=\n", key + "=" + secrets.token_hex(32) + "\n")
target.write_text(template)
target.chmod(0o600)
print("Created private .env. Set LLM_MODEL and LLM_API_KEY before live agent verification.")
