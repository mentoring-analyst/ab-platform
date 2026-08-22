import base64
import os
from pathlib import Path

import numpy as np
import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_simulation() -> dict:
    cfg = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text())
    cfg["accel"] = float(os.environ.get("SIM_ACCEL", 144))
    cfg["history_days"] = int(os.environ.get("HISTORY_DAYS", 45))
    cfg["pop_per_region"] = int(os.environ.get("POP_PER_REGION", 25000))
    for name in ("hour_profile_weekday", "hour_profile_weekend"):
        arr = np.array(cfg[name], dtype=float)
        cfg[name] = arr / arr.sum()
    cfg["surge_by_hour"] = np.array(cfg["surge_by_hour"], dtype=float)
    cfg["dow_multipliers"] = np.array(cfg["dow_multipliers"], dtype=float)
    return cfg


def load_scenario() -> dict:
    """Пакет сценария с эффектами вариантов. Хранится в base64, чтобы эффект
    эксперимента не было видно при случайном взгляде в репозиторий (декодировать
    можно, но это спойлер собственного обучения)."""
    path = CONFIG_DIR / "scenario.yaml.b64"
    if not path.exists():
        return {"experiments": {}}
    raw = base64.b64decode(path.read_text())
    return yaml.safe_load(raw) or {"experiments": {}}
