"""Acesso aos bancos: o próprio (billing.db) e o do painel itldesk-api (rustdeskapi.db)."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import bcrypt

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    max_technicians INTEGER NOT NULL,
    max_devices INTEGER,               -- NULL = ilimitado
    price_monthly INTEGER NOT NULL,    -- centavos
    price_yearly INTEGER NOT NULL,     -- centavos
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    document TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    rd_group_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL UNIQUE REFERENCES customers(id),
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    cycle TEXT NOT NULL DEFAULT 'monthly',      -- monthly | yearly
    status TEXT NOT NULL,                       -- pending | trial | active | past_due | blocked | canceled
    period_end TEXT NOT NULL,                   -- último dia pago (AAAA-MM-DD)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    public_token TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    amount INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | paid | canceled
    mp_preference_id TEXT,
    payment_url TEXT,
    mp_payment_id TEXT,
    paid_at TEXT,
    paid_method TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invoices_sub ON invoices(subscription_id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    at TEXT NOT NULL,
    message TEXT NOT NULL
);
"""

FREE_PLAN = ("Gratuito", "1 sessão por vez, de até 5 minutos", 1, 10, 0, 0, 0)

DEFAULT_PLANS = [
    FREE_PLAN,
    ("Básico", "Para quem atende sozinho", 1, 50, 4900, 49000, 1),
    ("Profissional", "Pequenas equipes de suporte", 3, 300, 12900, 129000, 2),
    ("Empresa", "Equipes maiores, dispositivos ilimitados", 10, None, 34900, 349000, 3),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def billing():
    conn = _connect(config.BILLING_DB)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def rdapi():
    conn = _connect(config.RDAPI_DB)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    os.makedirs(os.path.dirname(config.BILLING_DB) or ".", exist_ok=True)
    with billing() as db:
        db.executescript(SCHEMA)
        if db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0:
            db.executemany(
                "INSERT INTO plans (name, description, max_technicians, max_devices,"
                " price_monthly, price_yearly, sort) VALUES (?,?,?,?,?,?,?)",
                DEFAULT_PLANS,
            )
        # bancos criados antes do plano gratuito
        if db.execute("SELECT COUNT(*) FROM plans WHERE price_monthly = 0 AND price_yearly = 0").fetchone()[0] == 0:
            db.execute(
                "INSERT INTO plans (name, description, max_technicians, max_devices,"
                " price_monthly, price_yearly, sort) VALUES (?,?,?,?,?,?,?)",
                FREE_PLAN,
            )


def log_event(db: sqlite3.Connection, customer_id, message: str):
    db.execute(
        "INSERT INTO events (customer_id, at, message) VALUES (?,?,?)",
        (customer_id, now_iso(), message),
    )


# ---------------------------------------------------------------- painel (itldesk-api)

def _rd_time() -> str:
    # mesmo formato que o itldesk-api grava (ex.: 2026-11-04 17:14:07.851771-03:00)
    return datetime.now().astimezone().isoformat(sep=" ")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def rd_admin_login(username: str, password: str):
    with rdapi() as rd:
        row = rd.execute(
            "SELECT id, username, password FROM users WHERE username = ? AND is_admin = 1 AND status = 1",
            (username,),
        ).fetchone()
    if row and check_password(password, row["password"]):
        return {"id": row["id"], "username": row["username"]}
    return None


def rd_create_group(name: str) -> int:
    t = _rd_time()
    with rdapi() as rd:
        cur = rd.execute(
            "INSERT INTO groups (name, type, created_at, updated_at) VALUES (?, 1, ?, ?)",
            (name, t, t),
        )
        return cur.lastrowid


def rd_rename_group(group_id: int, name: str):
    with rdapi() as rd:
        rd.execute("UPDATE groups SET name = ?, updated_at = ? WHERE id = ?", (name, _rd_time(), group_id))


def rd_group_users(group_id):
    if not group_id:
        return []
    with rdapi() as rd:
        return [dict(r) for r in rd.execute(
            "SELECT id, username, email, nickname, status FROM users WHERE group_id = ? AND is_admin = 0 ORDER BY id",
            (group_id,),
        )]


def rd_username_exists(username: str) -> bool:
    with rdapi() as rd:
        return rd.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None


def rd_create_user(username: str, password: str, nickname: str, email: str, group_id: int) -> int:
    t = _rd_time()
    with rdapi() as rd:
        cur = rd.execute(
            "INSERT INTO users (username, email, password, nickname, avatar, group_id, is_admin, status,"
            " remark, created_at, updated_at) VALUES (?,?,?,?,'',?,0,1,'UnityDesk billing',?,?)",
            (username, email, hash_password(password), nickname, group_id, t, t),
        )
        return cur.lastrowid


def rd_set_password(user_id: int, group_id: int, password: str):
    with rdapi() as rd:
        rd.execute(
            "UPDATE users SET password = ?, updated_at = ? WHERE id = ? AND group_id = ? AND is_admin = 0",
            (hash_password(password), _rd_time(), user_id, group_id),
        )
        rd.execute("DELETE FROM user_tokens WHERE user_id = ?", (user_id,))


def rd_remove_user(user_id: int, group_id: int):
    """Desativa o técnico, tira do grupo do cliente e encerra as sessões."""
    with rdapi() as rd:
        rd.execute(
            "UPDATE users SET status = 2, group_id = 1, updated_at = ? WHERE id = ? AND group_id = ? AND is_admin = 0",
            (_rd_time(), user_id, group_id),
        )
        rd.execute("DELETE FROM user_tokens WHERE user_id = ?", (user_id,))


def rd_device_count(user_ids) -> int:
    if not user_ids:
        return 0
    marks = ",".join("?" * len(user_ids))
    with rdapi() as rd:
        # dispositivos no catálogo de endereços dos técnicos + máquinas em que eles estão logados
        return rd.execute(
            f"SELECT COUNT(*) FROM (SELECT id FROM address_books WHERE user_id IN ({marks})"
            f" UNION SELECT id FROM peers WHERE user_id IN ({marks}))",
            list(user_ids) * 2,
        ).fetchone()[0]


def rd_admin_ids():
    with rdapi() as rd:
        return [r[0] for r in rd.execute("SELECT id FROM users WHERE is_admin = 1 AND status = 1")]
