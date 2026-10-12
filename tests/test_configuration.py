"""The documented bootstrap must replace example secrets and preserve existing data."""
import importlib.util
from pathlib import Path
import stat

import pytest
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_script", ROOT / "scripts/configure.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_example_generates_private_configuration_without_fake_llm_key(tmp_path):
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text())
    target = module.configure(tmp_path)
    values = dotenv_values(target)
    generated = [values[key] for key in (
        "POSTGRES_PASSWORD", "AUTH_SECRET", "INTERNAL_TOKEN", "DEMO_ADMIN_PASSWORD"
    )]
    assert len(set(generated)) == 4
    assert all(len(value) == 64 and all(c in "0123456789abcdef" for c in value) for value in generated)
    assert values["LLM_API_KEY"] == ""
    assert values["LLM_MODEL"] == "glm-5.3-flash"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_configuration_cannot_replace_existing_database_credentials(tmp_path):
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text())
    target = tmp_path / ".env"
    target.write_text("POSTGRES_PASSWORD=existing-database-password\n")
    with pytest.raises(FileExistsError):
        module.configure(tmp_path)
    assert target.read_text() == "POSTGRES_PASSWORD=existing-database-password\n"
