import os

import requests

BASE = os.environ.get("SPLITTER_URL", "http://splitter:8000")
_session = requests.Session()


def running_experiments() -> list[dict]:
    resp = _session.get(f"{BASE}/experiments", params={"status": "running"}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def batch_assign(code: str, user_ids: list[int], virtual_ts: str) -> dict[int, str]:
    """Возвращает {user_id: variant} для попавших в аудиторию."""
    resp = _session.post(
        f"{BASE}/experiments/{code}/variants/batch",
        json={"user_ids": user_ids, "virtual_ts": virtual_ts},
        timeout=60,
    )
    resp.raise_for_status()
    return {int(uid): variant for uid, variant in resp.json()["assignments"].items()}
