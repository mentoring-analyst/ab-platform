import os
import time

import clickhouse_connect
import psycopg2
from psycopg2.extras import execute_values

EVENT_COLUMNS = [
    "event_date", "event_ts", "user_id", "session_id", "event_name",
    "region", "platform", "tariff",
    "price_low", "price_high", "price_shown", "price_actual",
]


def pg_connect(retries=30):
    for attempt in range(retries):
        try:
            return psycopg2.connect(os.environ["PG_DSN"])
        except psycopg2.OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def ch_connect(retries=30):
    for attempt in range(retries):
        try:
            return clickhouse_connect.get_client(
                host=os.environ["CH_HOST"],
                port=int(os.environ.get("CH_PORT", 8123)),
                username=os.environ.get("CH_USER", "default"),
                password=os.environ.get("CH_PASSWORD", ""),
            )
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def insert_events(ch, rows):
    if rows:
        ch.insert("ab.events", rows, column_names=EVENT_COLUMNS)


def users_count(pg) -> int:
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.users")
        return cur.fetchone()[0]


def write_users(pg, sim, chunk=20000):
    rows = [
        (int(sim.uids[i]), sim.region_codes[sim.region_idx[i]], str(sim.platform[i]), sim.signup_dates[i])
        for i in range(sim.n)
    ]
    with pg.cursor() as cur:
        for start in range(0, len(rows), chunk):
            execute_values(
                cur,
                "INSERT INTO core.users (user_id, region_code, platform, signup_date) VALUES %s "
                "ON CONFLICT (user_id) DO NOTHING",
                rows[start:start + chunk],
            )
    pg.commit()


def write_user_stats(pg, sim, sessions, trips, chunk=20000):
    rows = [(int(sim.uids[i]), int(sessions[i]), int(trips[i])) for i in range(sim.n)]
    with pg.cursor() as cur:
        for start in range(0, len(rows), chunk):
            execute_values(
                cur,
                "INSERT INTO core.user_stats (user_id, sessions_preperiod, trips_preperiod) VALUES %s "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "sessions_preperiod = EXCLUDED.sessions_preperiod, "
                "trips_preperiod = EXCLUDED.trips_preperiod",
                rows[start:start + chunk],
            )
    pg.commit()


def get_clock(pg):
    with pg.cursor() as cur:
        cur.execute("SELECT virtual_now FROM ab.sim_clock WHERE id = 1")
        row = cur.fetchone()
        return row[0] if row else None


def set_clock(pg, virtual_now, accel):
    with pg.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ab.sim_clock (id, virtual_now, accel, updated_real_at)
            VALUES (1, %s, %s, now())
            ON CONFLICT (id) DO UPDATE
                SET virtual_now = EXCLUDED.virtual_now,
                    accel = EXCLUDED.accel,
                    updated_real_at = now()
            """,
            (virtual_now, accel),
        )
    pg.commit()
