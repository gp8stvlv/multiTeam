from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select

from .models import DishRow, EvidenceRow, ImportRow, MenuItemRow, MenuVersionRow, RestaurantRow, ReviewRow


REVISION_MODELS = (RestaurantRow, DishRow, ImportRow, MenuVersionRow, MenuItemRow, EvidenceRow, ReviewRow)


def _source_revision():
    digest = hashlib.sha256()
    for source in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(source.name.encode("utf-8") + b"\0" + source.read_bytes() + b"\0")
    return digest.hexdigest()


SOURCE_REVISION = _source_revision()


def _json_value(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="microseconds")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"sha256": hashlib.sha256(value).hexdigest()}
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Unsupported catalog revision value: {type(value).__name__}")


def recommendation_revision(session, embedding_model: str) -> str:
    """Conservatively invalidate recommendations after any catalog data change."""
    digest = hashlib.sha256()

    def include(value):
        digest.update(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                 default=_json_value, allow_nan=False).encode("utf-8"))
        digest.update(b"\n")

    include({"format": "recommendation-revision-v1", "code_revision": SOURCE_REVISION,
             "embedding_model": embedding_model,
             "embedding_provider": os.getenv("CATALOG_EMBEDDING_PROVIDER", "fastembed")})
    for model in REVISION_MODELS:
        include({"table": model.__tablename__})
        for row in session.execute(select(model.__table__).order_by(model.id)).mappings():
            include(dict(row))
    return digest.hexdigest()
