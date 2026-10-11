from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from services.users.nutrition import aggregate_nutrition, calculate_price, utc_day_bounds
from services.users.schemas import NutritionFacts, ProfilePatch


def facts(calories="620", protein="32", fat="22", carbs="73.5", source="restaurant_declared"):
    return NutritionFacts(
        calories_kcal=calories, protein_g=protein, fat_g=fat, carbs_g=carbs,
        source=source, basis="per_portion", evidence_ids=[],
    )


def test_fractional_consumption_is_exact_not_binary_float():
    totals = aggregate_nutrition([(facts(), Decimal("0.5"))])
    assert totals.calories_kcal == Decimal("310")
    assert totals.carbs_g == Decimal("36.75")
    precise = aggregate_nutrition([(facts(calories="3.33333333"), Decimal("0.33333333"))])
    assert precise.calories_kcal == Decimal("1.1111110988888889")


def test_partial_coverage_is_individual_per_nutrient():
    totals = aggregate_nutrition([
        (facts(), Decimal("0.5")),
        (facts(calories=None, protein="10", fat=None, carbs="0", source="user_entered"), Decimal("2")),
    ])
    assert totals.calories_kcal is None
    assert totals.known_subtotals.calories_kcal == Decimal("310")
    assert totals.fat_g is None
    assert totals.protein_g == Decimal("36")
    assert totals.carbs_g == Decimal("36.75")
    assert totals.complete_fields == ["protein_g", "carbs_g"]
    assert set(totals.sources) == {"restaurant_declared", "user_entered"}


def test_empty_day_has_zero_known_totals():
    totals = aggregate_nutrition([])
    assert totals.calories_kcal == 0
    assert len(totals.complete_fields) == 4
    assert totals.sources == []


def test_price_rounds_total_once_half_up():
    assert calculate_price([(75001, Decimal("0.5"))]) == 37501
    assert calculate_price([(1, Decimal("0.5")), (1, Decimal("0.5"))]) == 1


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_dst_day_uses_local_midnights(day, hours):
    start, end = utc_day_bounds(day, "America/New_York")
    assert end - start == timedelta(hours=hours)


def test_profile_patch_rejects_invalid_timezone_and_duplicate_allergies():
    with pytest.raises(ValidationError):
        ProfilePatch(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        ProfilePatch(allergies=[{"code": "peanut"}, {"code": "peanut"}])
    assert ProfilePatch(disliked_ingredients=["  кинза  "]).disliked_ingredients == ["кинза"]


def test_unknown_source_cannot_claim_numbers():
    with pytest.raises(ValidationError):
        facts(source="unknown")
    with pytest.raises(ValidationError):
        facts(source="estimated")
