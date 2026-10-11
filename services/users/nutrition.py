"""Exact arithmetic shared by diary and meal calculations; no model-generated numbers."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Iterable
from zoneinfo import ZoneInfo

from .schemas import NutritionFacts, NutritionTotals

NUTRIENTS = ("calories_kcal", "protein_g", "fat_g", "carbs_g")


def aggregate_nutrition(items: Iterable[tuple[NutritionFacts, Decimal]]) -> NutritionTotals:
    subtotals = {name: Decimal(0) for name in NUTRIENTS}
    complete = dict.fromkeys(NUTRIENTS, True)
    sources = set()
    with localcontext() as context:
        context.prec = 50
        for facts, portions in items:
            sources.add(facts.source)
            for name in NUTRIENTS:
                value = getattr(facts, name)
                if value is None:
                    complete[name] = False
                else:
                    subtotals[name] += value * portions
    return NutritionTotals(
        **{name: subtotals[name] if complete[name] else None for name in NUTRIENTS},
        known_subtotals=subtotals,
        complete_fields=[name for name in NUTRIENTS if complete[name]],
        sources=sorted(sources),
    )


def calculate_price(items: Iterable[tuple[int, Decimal]]) -> int:
    with localcontext() as context:
        context.prec = 50
        total = sum((Decimal(price) * portions for price, portions in items), Decimal(0))
        return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def utc_day_bounds(day: date, zone: str) -> tuple[datetime, datetime]:
    """Local midnights are built separately, respecting 23/25-hour DST days."""
    tz = ZoneInfo(zone)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def utc_datetime(value: datetime) -> datetime:
    # SQLite does not retain DateTime timezone metadata; every stored time is UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
