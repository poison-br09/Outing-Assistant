"""Small shared helpers."""
import math
from typing import Iterable, TypeVar

T = TypeVar("T")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _matches(category: str, prefix: str) -> bool:
    return category == prefix or category.startswith(prefix + ".")


def category_lookup(table: dict[str, T], categories: Iterable[str], default: T) -> T:
    """Value of the longest table prefix matching any of the categories."""
    best, best_len = default, -1
    for c in categories:
        for prefix, value in table.items():
            if _matches(c, prefix) and len(prefix) > best_len:
                best, best_len = value, len(prefix)
    return best


def has_category(categories: Iterable[str], prefixes: Iterable[str]) -> bool:
    cats = list(categories)
    return any(_matches(c, p) for c in cats for p in prefixes)
