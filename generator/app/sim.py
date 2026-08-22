import hashlib
from datetime import date, datetime, timedelta

import numpy as np

DISPLAY_MODES = {"range": 0, "point": 1, "point_low": 2}


class Simulation:
    """Популяция пользователей и механика воронки.

    Латентные характеристики (частота сессий, чувствительность к цене) не хранятся
    в базе — они детерминированно выводятся из глобального сида, поэтому перезапуск
    контейнера воспроизводит ту же популяцию. Сиды дневных/часовых тиков зависят от
    даты, поэтому перезапуск не «переигрывает» уже сгенерированную историю иначе.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.region_codes = list(cfg["regions"])
        self.n = cfg["pop_per_region"] * len(self.region_codes)
        self.uids = np.arange(1, self.n + 1, dtype=np.int64)
        self.region_idx = ((self.uids - 1) % len(self.region_codes)).astype(int)

        rng = np.random.default_rng(cfg["seed"])
        ul = cfg["user_latents"]
        self.session_rate = np.minimum(
            rng.gamma(ul["session_rate_gamma_shape"], ul["session_rate_gamma_scale"], self.n),
            ul["session_rate_cap"],
        )
        self.price_sens = np.clip(
            rng.normal(ul["price_sens_mean"], ul["price_sens_std"], self.n), 0.3, 2.2
        )
        self.platform = np.where(rng.random(self.n) < cfg["platform_ios_share"], "ios", "android")
        epoch = date.fromisoformat(cfg["sim_start_date"])
        self.signup_dates = [epoch - timedelta(days=int(d)) for d in rng.integers(1, 730, self.n)]

        self.price_mu = np.array([cfg["regions"][r]["price_mu"] for r in self.region_codes])
        tariffs = cfg["tariffs"]
        self.tariff_names = list(tariffs)
        self.tariff_shares = np.array([tariffs[t]["share"] for t in self.tariff_names])
        self.tariff_shares /= self.tariff_shares.sum()
        self.tariff_mult = np.array([tariffs[t]["multiplier"] for t in self.tariff_names])

    def _tick_rng(self, d: date, hour=None) -> np.random.Generator:
        key = f"{self.cfg['seed']}:{d.isoformat()}:{hour}"
        seed = int.from_bytes(hashlib.blake2s(key.encode()).digest()[:8], "little")
        return np.random.default_rng(seed)

    def _hour_profile(self, d: date) -> np.ndarray:
        return self.cfg["hour_profile_weekend" if d.weekday() >= 5 else "hour_profile_weekday"]

    def sessions_for_day(self, d: date):
        """Все сессии виртуального дня (режим бэкфилла истории)."""
        rng = self._tick_rng(d)
        lam = self.session_rate * self.cfg["dow_multipliers"][d.weekday()]
        counts = rng.poisson(lam)
        idx = np.nonzero(counts)[0]
        user_pos = np.repeat(idx, counts[idx])
        hours = rng.choice(24, size=len(user_pos), p=self._hour_profile(d))
        return rng, user_pos, hours

    def sessions_for_hour(self, d: date, hour: int):
        """Сессии одного виртуального часа (живой режим)."""
        rng = self._tick_rng(d, hour)
        lam = (
            self.session_rate
            * self.cfg["dow_multipliers"][d.weekday()]
            * self._hour_profile(d)[hour]
        )
        counts = rng.poisson(lam)
        idx = np.nonzero(counts)[0]
        user_pos = np.repeat(idx, counts[idx])
        hours = np.full(len(user_pos), hour, dtype=int)
        return rng, user_pos, hours

    def simulate(self, rng, d: date, user_pos, hours, effects=None):
        """Прогоняет сессии по воронке. effects: {user_id: конфиг варианта из сценария}.

        Возвращает (rows для ab.events, сессий на пользователя, поездок на пользователя).
        """
        f = self.cfg["funnel"]
        n = len(user_pos)
        rows = []
        sessions_by_user = np.bincount(user_pos, minlength=self.n)
        trips_by_user = np.zeros(self.n, dtype=np.int64)
        if n == 0:
            return rows, sessions_by_user, trips_by_user

        uids = self.uids[user_pos]
        regions_i = self.region_idx[user_pos]
        sens = self.price_sens[user_pos]
        minutes = rng.integers(0, 60, n)
        seconds = rng.integers(0, 60, n)
        tariff_i = rng.choice(len(self.tariff_names), size=n, p=self.tariff_shares)
        surge = self.cfg["surge_by_hour"][hours]
        base_price = np.exp(rng.normal(self.price_mu[regions_i], self.cfg["price_sigma"]))
        price_est = base_price * surge * self.tariff_mult[tariff_i]

        display = np.zeros(n, dtype=int)
        order_uplift = np.zeros(n)
        cancel_delta = np.zeros(n)
        if effects:
            for j in range(n):
                eff = effects.get(int(uids[j]))
                if eff:
                    display[j] = DISPLAY_MODES.get(eff.get("display", "range"), 0)
                    order_uplift[j] = eff.get("order_uplift_pp", 0.0) / 100.0
                    cancel_delta[j] = eff.get("cancel_delta_pp", 0.0) / 100.0

        price_low = np.round(price_est * self.cfg["price_range_low"], 2)
        price_high = np.round(price_est * self.cfg["price_range_high"], 2)
        price_point = np.round(np.where(display == 2, price_est * 0.93, price_est), 2)

        r_screen = rng.random(n)
        r_select = rng.random(n)
        r_order = rng.random(n)
        r_cancel = rng.random(n)
        p_order = np.clip(
            f["p_order_base"] - f["price_beta"] * sens * (surge - 1.0) + order_uplift, 0.03, 0.97
        )
        p_cancel = np.clip(f["p_cancel"] + cancel_delta, 0.005, 0.9)
        actual_noise = rng.normal(0, f["price_noise_sigma"], n)

        for j in range(n):
            uid = int(uids[j])
            reg = self.region_codes[regions_i[j]]
            plat = str(self.platform[user_pos[j]])
            tar = self.tariff_names[tariff_i[j]]
            t0 = datetime(d.year, d.month, d.day, int(hours[j]), int(minutes[j]), int(seconds[j]))
            sid = f"{uid}-{d.isoformat()}-{int(hours[j])}-{j}"

            def add(name, offset_s, plow=None, phigh=None, pshown=None, pactual=None):
                t = t0 + timedelta(seconds=offset_s)
                rows.append((t.date(), t, uid, sid, name, reg, plat, tar, plow, phigh, pshown, pactual))

            add("app_open", 0)
            if r_screen[j] >= f["p_screen_view"]:
                continue
            if display[j] == 0:
                pl, ph, ps = float(price_low[j]), float(price_high[j]), None
            else:
                pl, ph, ps = None, None, float(price_point[j])
            add("screen_view", 5, plow=pl, phigh=ph, pshown=ps)
            if r_select[j] >= f["p_tariff_select"]:
                continue
            add("tariff_select", 25, plow=pl, phigh=ph, pshown=ps)
            if r_order[j] >= p_order[j]:
                continue
            add("order_confirm", 40, plow=pl, phigh=ph, pshown=ps)
            if r_cancel[j] < p_cancel[j]:
                add("order_cancelled", 130)
            else:
                actual = round(float(price_est[j] * (1.0 + actual_noise[j])), 2)
                add("trip_complete", 900, pactual=actual)
                trips_by_user[user_pos[j]] += 1

        return rows, sessions_by_user, trips_by_user
