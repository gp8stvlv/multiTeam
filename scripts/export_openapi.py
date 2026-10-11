"""Export real implemented schemas without starting workers or requiring secrets."""
import importlib
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ["APP_ENV"] = "development"
for service in ("CATALOG", "USERS", "RECOMMENDATIONS"):
    os.environ[service + "_DATABASE_URL"] = "sqlite:///:memory:"
target = root / "docs" / "openapi"
target.mkdir(exist_ok=True)
for service in ("catalog", "users", "recommendations"):
    app = importlib.import_module(f"services.{service}.app").app
    schema = app.openapi()
    (target / f"{service}.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2))
    print(service, len(schema["paths"]), "paths exported")
