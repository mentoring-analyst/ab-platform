import mmh3

BUCKETS = 10000


def bucket(salt: str, user_id: int) -> int:
    """Детерминированный бакет: один и тот же (соль, пользователь) -> всегда один бакет."""
    return mmh3.hash(f"{salt}:{user_id}", signed=False) % BUCKETS


def pick_variant(variants: list[dict], b: int) -> str | None:
    """variants: [{"name": "A", "share": 34}, ...] — доли в процентах от ВСЕГО
    трафика аудитории. Сумма может быть меньше 100: пользователь с бакетом за
    пределами суммарной доли в эксперимент не попадает (None)."""
    edge = 0
    for v in variants:
        edge += v["share"] * BUCKETS // 100
        if b < edge:
            return v["name"]
    return None
