import time
from datetime import date, datetime, timedelta

import numpy as np
import requests

from . import config, sinks, splitter_client
from .sim import Simulation


def backfill_history(sim: Simulation, pg, ch, cfg: dict):
    epoch = date.fromisoformat(cfg["sim_start_date"])
    days = cfg["history_days"]
    print(f"[generator] бэкфилл истории: {days} виртуальных дней с {epoch}, популяция {sim.n} пользователей")
    t_start = time.time()
    total_sessions = np.zeros(sim.n, dtype=np.int64)
    total_trips = np.zeros(sim.n, dtype=np.int64)
    for i in range(days):
        d = epoch + timedelta(days=i)
        rng, user_pos, hours = sim.sessions_for_day(d)
        rows, sessions, trips = sim.simulate(rng, d, user_pos, hours)
        sinks.insert_events(ch, rows)
        total_sessions += sessions
        total_trips += trips
        if (i + 1) % 10 == 0 or i == days - 1:
            print(f"[generator] история: день {i + 1}/{days} ({d}), событий за день: {len(rows)}")
    sinks.write_user_stats(pg, sim, total_sessions, total_trips)
    live_start = datetime.combine(epoch + timedelta(days=days), datetime.min.time())
    sinks.set_clock(pg, live_start, cfg["accel"])
    print(f"[generator] история готова за {time.time() - t_start:.0f} с; живое время начинается с {live_start}")


def live_tick(sim: Simulation, ch, scenario: dict, virtual_now: datetime) -> int:
    d, hour = virtual_now.date(), virtual_now.hour
    rng, user_pos, hours = sim.sessions_for_hour(d, hour)

    # Сплитование активных в этом часе пользователей по всем идущим экспериментам
    effects = {}
    if len(user_pos):
        active_ids = sorted({int(u) for u in sim.uids[user_pos]})
        try:
            for exp in splitter_client.running_experiments():
                variant_effects = scenario.get("experiments", {}).get(exp["code"], {}).get("variants", {})
                assignments = splitter_client.batch_assign(exp["code"], active_ids, virtual_now.isoformat())
                for uid, variant in assignments.items():
                    # пользователь в нескольких экспериментах — применяем первый (регионы студентов не пересекаются)
                    if uid not in effects:
                        effects[uid] = variant_effects.get(variant, {})
        except requests.RequestException as e:
            print(f"[generator] сплитовалка недоступна, тик без экспериментов: {e!r}")

    rows, _, _ = sim.simulate(rng, d, user_pos, hours, effects=effects)
    sinks.insert_events(ch, rows)
    return len(rows)


def main():
    cfg = config.load_simulation()
    scenario = config.load_scenario()
    sim = Simulation(cfg)
    pg = sinks.pg_connect()
    ch = sinks.ch_connect()

    if sinks.users_count(pg) == 0:
        print("[generator] заполняю core.users и справочники популяции")
        sinks.write_users(pg, sim)

    clock = sinks.get_clock(pg)
    if clock is None:
        backfill_history(sim, pg, ch, cfg)
        clock = sinks.get_clock(pg)

    tick_real = 3600.0 / cfg["accel"]
    print(f"[generator] живой режим: 1 виртуальный час = {tick_real:.1f} реальных секунд "
          f"(1 день = {tick_real * 24 / 60:.1f} минут)")

    while True:
        t0 = time.time()
        try:
            if clock.hour == 0:
                print(f"[generator] виртуальный день {clock.date()}")
            n_rows = live_tick(sim, ch, scenario, clock)
            next_clock = clock + timedelta(hours=1)
            sinks.set_clock(pg, next_clock, cfg["accel"])
            clock = next_clock
        except Exception as e:
            print(f"[generator] ошибка тика {clock}: {e!r}; переподключаюсь")
            time.sleep(3)
            try:
                pg = sinks.pg_connect()
                ch = sinks.ch_connect()
            except Exception as e2:
                print(f"[generator] переподключение не удалось: {e2!r}")
        elapsed = time.time() - t0
        time.sleep(max(0.5, tick_real - elapsed))


if __name__ == "__main__":
    main()
