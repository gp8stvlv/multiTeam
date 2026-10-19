"""Request-local, reversible names for opaque identifiers in model context."""
from __future__ import annotations

from collections.abc import Collection
from copy import deepcopy
from typing import Any
from uuid import UUID


class RecommendationReferences:
    KINDS = frozenset({"m", "d", "r", "e", "v"})
    FIELD_KINDS = {
        "menu_item_id": "m",
        "dish_id": "d",
        "restaurant_id": "r",
        "evidence_id": "e",
    }

    def __init__(self) -> None:
        self._aliases: dict[str, dict[str, str]] = {kind: {} for kind in self.KINDS}
        self._originals: dict[str, dict[str, str]] = {kind: {} for kind in self.KINDS}

    def _reference(self, value: Any, kind: str) -> Any:
        if not isinstance(value, str) or not value:
            return deepcopy(value)
        aliases = self._aliases[kind]
        if value not in aliases:
            reference = f"{kind}{len(aliases) + 1}"
            aliases[value] = reference
            self._originals[kind][reference] = value
        return aliases[value]

    def encode(self, value: Any, *, id_kind: str | None = None) -> Any:
        """Copy JSON, replacing only declared ID fields with typed references.

        ``id_kind`` declares the kind of generic ``id`` fields in the supplied
        entity or collection; the ``restaurants`` collection always uses ``r``.
        It does not affect explicit fields such as ``menu_item_id``.
        """
        if id_kind is not None and id_kind not in self.KINDS:
            raise ValueError("Unknown identifier kind")
        if isinstance(value, list):
            return [self.encode(item, id_kind=id_kind) for item in value]
        if not isinstance(value, dict):
            return deepcopy(value)
        encoded = {}
        for key, item in value.items():
            if key in self.FIELD_KINDS:
                encoded[key] = self._reference(item, self.FIELD_KINDS[key])
            elif key == "evidence_ids" and isinstance(item, list):
                encoded[key] = [self._reference(reference, "e") for reference in item]
            elif key == "id" and id_kind is not None:
                encoded[key] = self._reference(item, id_kind)
            elif key == "restaurants" and isinstance(item, list):
                encoded[key] = self.encode(item, id_kind="r")
            else:
                encoded[key] = self.encode(item, id_kind=id_kind)
        return encoded

    def _resolve(self, reference: str, allowed_ids: Collection[str], kind: str) -> str:
        if not isinstance(reference, str):
            raise ValueError("Invalid identifier reference")
        original = self._originals[kind].get(reference)
        if original is not None:
            if original in allowed_ids:
                return original
            raise ValueError("Identifier is not an allowed candidate")
        # Canonical IDs remain accepted for existing clients and scripted models,
        # but an opaque alias must have been issued by this request's registry.
        try:
            UUID(reference)
        except ValueError:
            raise ValueError("Unknown identifier reference") from None
        if reference not in allowed_ids:
            raise ValueError("Identifier is not an allowed candidate")
        return reference

    def resolve_menu(self, ref: str, allowed_ids: Collection[str]) -> str:
        return self._resolve(ref, allowed_ids, "m")

    def resolve_restaurant(self, ref: str, allowed_ids: Collection[str]) -> str:
        return self._resolve(ref, allowed_ids, "r")
