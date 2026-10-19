from copy import deepcopy
from uuid import uuid4

import pytest

from services.recommendations.references import RecommendationReferences


def test_encoded_candidates_retain_all_values_and_identifier_relationships():
    menu, dish, restaurant, evidence = [str(uuid4()) for _ in range(4)]
    original = {
        "items": [{
            "menu_item_id": menu, "dish_id": dish, "restaurant_id": restaurant,
            "evidence_ids": [evidence, evidence], "evidence_count": 1,
            "allergens": [{"code": "peanut", "status": "unknown", "evidence_id": evidence}],
            "nutrition_per_portion": {"calories_kcal": None, "protein_g": 0, "source": "unknown"},
            "ingredients_complete": False, "warnings": [], "truncated_fields": ["ingredients"],
        }],
        "restaurants": [{"id": restaurant, "name": "Restaurant", "rating": None}],
        "candidate_count": 1, "omitted_count": 0,
    }
    references = RecommendationReferences()
    encoded = references.encode(original)
    item = encoded["items"][0]
    assert item["restaurant_id"] == encoded["restaurants"][0]["id"] == "r1"
    assert item["evidence_ids"] == ["e1", "e1"]
    assert item["allergens"][0]["evidence_id"] == "e1"
    inverse = {item["menu_item_id"]: menu, item["dish_id"]: dish,
               item["restaurant_id"]: restaurant, item["evidence_ids"][0]: evidence}

    def restore(value):
        if isinstance(value, dict):
            return {key: restore(item) for key, item in value.items()}
        if isinstance(value, list):
            return [restore(item) for item in value]
        return inverse.get(value, value) if isinstance(value, str) else value

    assert restore(encoded) == original
    assert references.resolve_menu(item["menu_item_id"], {menu}) == menu
    assert references.resolve_restaurant(item["restaurant_id"], {restaurant}) == restaurant


def test_references_remain_stable_after_refinement_and_reordering():
    first, second, third = [str(uuid4()) for _ in range(3)]
    references = RecommendationReferences()
    assert references.encode([{"menu_item_id": first}, {"menu_item_id": second}]) == [
        {"menu_item_id": "m1"}, {"menu_item_id": "m2"},
    ]
    assert references.encode([{"menu_item_id": third}, {"menu_item_id": second}, {"menu_item_id": first}]) == [
        {"menu_item_id": "m3"}, {"menu_item_id": "m2"}, {"menu_item_id": "m1"},
    ]
    assert references.resolve_menu("m2", {first, second, third}) == second
    assert RecommendationReferences().encode({"menu_item_id": third}) == {"menu_item_id": "m1"}


def test_generic_ids_require_a_declared_kind_and_text_is_never_rewritten():
    source = str(uuid4())
    references = RecommendationReferences()
    original = {"id": source, "query": source, "name": source, "text": source,
                "items": [{"id": source, "text": "m1 r1 e1"}]}
    assert references.encode(original) == original
    encoded = references.encode(original, id_kind="v")
    assert encoded == {"id": "v1", "query": source, "name": source, "text": source,
                       "items": [{"id": "v1", "text": "m1 r1 e1"}]}


def test_explicit_fields_and_restaurants_override_the_declared_generic_kind():
    source = str(uuid4())
    encoded = RecommendationReferences().encode({
        "id": source, "menu_item_id": source, "dish_id": source,
        "restaurant_id": source, "evidence_id": source,
        "restaurants": [{"id": source}],
    }, id_kind="v")
    assert encoded == {"id": "v1", "menu_item_id": "m1", "dish_id": "d1",
                       "restaurant_id": "r1", "evidence_id": "e1",
                       "restaurants": [{"id": "r1"}]}


@pytest.mark.parametrize("method,kind", [("resolve_menu", "m"), ("resolve_restaurant", "r")])
def test_resolution_is_exact_and_restricted_to_the_correct_allowed_namespace(method, kind):
    allowed, excluded = str(uuid4()), str(uuid4())
    references = RecommendationReferences()
    references.encode({"id": allowed}, id_kind=kind)
    references.encode({"id": excluded}, id_kind=kind)
    resolve = getattr(references, method)
    assert resolve(f"{kind}1", {allowed}) == allowed
    assert resolve(allowed, {allowed}) == allowed
    for rejected in [f"{kind}2", f"{kind}99", f"{kind}01", f"{kind.upper()}1",
                     f" {kind}1", f"{kind}1 ", excluded, str(uuid4()), "unknown", None, False, 0, [], {}]:
        with pytest.raises(ValueError):
            resolve(rejected, {allowed})
    for other_kind in RecommendationReferences.KINDS - {kind}:
        references.encode({"id": allowed}, id_kind=other_kind)
        with pytest.raises(ValueError):
            resolve(f"{other_kind}1", {allowed})


def test_encoding_copies_source_and_preserves_null_boolean_zero_and_lists():
    source = {"id": str(uuid4()), "items": [{"menu_item_id": None, "dish_id": False,
              "restaurant_id": 0, "evidence_ids": [None, False, 0, ""], "warnings": []}],
              "evidence_id": "", "nested": [{"values": [None, False, 0]}]}
    snapshot = deepcopy(source)
    encoded = RecommendationReferences().encode(source)
    assert source == snapshot == encoded
    assert encoded is not source
    assert encoded["items"] is not source["items"]
    assert encoded["items"][0] is not source["items"][0]
    encoded["nested"][0]["values"].append("changed")
    encoded["items"][0]["warnings"].append("changed")
    assert source == snapshot


def test_unknown_generic_identifier_kind_is_rejected():
    with pytest.raises(ValueError, match="Unknown identifier kind"):
        RecommendationReferences().encode({"id": str(uuid4())}, id_kind="menu")
