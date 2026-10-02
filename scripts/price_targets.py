"""Currency rounding and target construction shared by entry strategies.

Target returns are calculated from the displayed price after rounding.
"""
import math


def _krw_tick_size(price: float) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _round_price(value: float, currency: str = "USD", direction: str = "nearest") -> float:
    currency = str(currency or "USD").upper()
    if currency == "KRW":
        tick = _krw_tick_size(value)
        match direction:
            case "up":
                return float(math.ceil(value / tick) * tick)
            case "down":
                return float(math.floor(value / tick) * tick)
            case "nearest":
                return float(round(value / tick) * tick)
            case _:
                return float(round(value / tick) * tick)
    if currency == "JPY":
        return float(round(value))
    return round(float(value), 2)


def make_target(level: int, price: float, entry: float, source: str, currency: str) -> dict:
    """Build a target whose gain agrees with its currency-rounded price."""
    target_price = _round_price(price, currency, "down")
    return {
        "level": level,
        "price": target_price,
        "gain_pct": round((target_price / entry - 1) * 100, 2),
        "source": source,
    }


def resistance_targets(
    entry: float, resistances: list, currency: str, *,
    limit: int, above: float | None = None, first_level: int = 1,
) -> list[dict]:
    """Select ascending, distinct rounded targets above the given price.

    Advance through raw resistance levels, not by looking up the last rounded
    price: several raw levels may round to the same currency tick.
    """
    floor = entry if above is None else above
    candidates = sorted(
        (r for r in resistances
         if isinstance(r.get("price"), (int, float)) and r["price"] > floor),
        key=lambda r: r["price"],
    )
    targets = []
    for resistance in candidates:
        if len(targets) >= limit:
            break
        target = make_target(
            first_level + len(targets), resistance["price"], entry,
            resistance.get("source", "resistance"), currency,
        )
        if target["price"] <= floor:
            continue
        targets.append(target)
        floor = target["price"]
    return targets
