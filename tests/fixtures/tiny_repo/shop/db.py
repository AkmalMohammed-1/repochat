"""Order storage in SQLite."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    customer_email TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);
"""


def connect(path: str = "shop.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def save_order(conn: sqlite3.Connection, customer_email: str, total_cents: int) -> int:
    cur = conn.execute(
        "INSERT INTO orders (customer_email, total_cents) VALUES (?, ?)", (customer_email, total_cents)
    )
    conn.commit()
    return cur.lastrowid


def get_order(conn: sqlite3.Connection, order_id: int) -> tuple | None:
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


if __name__ == "__main__":
    connect()
    print("database ready")
