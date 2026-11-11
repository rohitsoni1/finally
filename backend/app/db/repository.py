"""Repository functions over the FinAlly SQLite database.

All functions are synchronous, take ``user_id="default"`` and an optional ``conn``.
Without ``conn`` each call opens its own connection (autocommit). Pass the connection
from ``transaction()`` to group several writes atomically.

Return shapes are plain dicts (JSON-serialisable). Tickers are stored stripped/uppercased.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .connection import DEFAULT_CASH, DEFAULT_USER_ID, get_connection, new_id, now_iso, transaction

# Remaining quantities at or below this are treated as zero (float dust from fractional sells).
QUANTITY_EPSILON = 1e-9


@contextmanager
def _use(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
    else:
        with get_connection() as c:
            yield c


def _norm(ticker: str) -> str:
    return ticker.strip().upper()


# ---------------------------------------------------------------- cash / user profile

def get_cash(user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None) -> float:
    """Cash balance for the user. Creates the profile ($10,000) if it does not exist."""
    with _use(conn) as c:
        row = c.execute("SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            c.execute(
                "INSERT OR IGNORE INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
                (user_id, DEFAULT_CASH, now_iso()),
            )
            return DEFAULT_CASH
        return float(row["cash_balance"])


def set_cash(amount: float, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None) -> float:
    with _use(conn) as c:
        cur = c.execute("UPDATE users_profile SET cash_balance = ? WHERE id = ?", (float(amount), user_id))
        if cur.rowcount == 0:
            c.execute(
                "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
                (user_id, float(amount), now_iso()),
            )
    return float(amount)


# ---------------------------------------------------------------- positions

def _position(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "quantity": float(row["quantity"]),
        "avg_cost": float(row["avg_cost"]),
        "updated_at": row["updated_at"],
    }


def get_positions(user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """All positions, ordered by ticker: [{ticker, quantity, avg_cost, updated_at}]."""
    with _use(conn) as c:
        rows = c.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()
    return [_position(r) for r in rows]


def get_position(
    ticker: str, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    with _use(conn) as c:
        row = c.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, _norm(ticker)),
        ).fetchone()
    return _position(row) if row else None


def upsert_position(
    ticker: str,
    quantity: float,
    avg_cost: float,
    user_id: str = DEFAULT_USER_ID,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    ts = now_iso()
    t = _norm(ticker)
    with _use(conn) as c:
        c.execute(
            """INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, ticker) DO UPDATE SET
                   quantity = excluded.quantity,
                   avg_cost = excluded.avg_cost,
                   updated_at = excluded.updated_at""",
            (new_id(), user_id, t, float(quantity), float(avg_cost), ts),
        )
    return {"ticker": t, "quantity": float(quantity), "avg_cost": float(avg_cost), "updated_at": ts}


def delete_position(ticker: str, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None) -> bool:
    """Returns True if a row was deleted."""
    with _use(conn) as c:
        cur = c.execute("DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, _norm(ticker)))
    return cur.rowcount > 0


# ---------------------------------------------------------------- trades

def _trade(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "side": row["side"],
        "quantity": float(row["quantity"]),
        "price": float(row["price"]),
        "executed_at": row["executed_at"],
    }


def insert_trade(
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    user_id: str = DEFAULT_USER_ID,
    conn: sqlite3.Connection | None = None,
    executed_at: str | None = None,
) -> dict[str, Any]:
    trade = {
        "id": new_id(),
        "ticker": _norm(ticker),
        "side": side.lower(),
        "quantity": float(quantity),
        "price": float(price),
        "executed_at": executed_at or now_iso(),
    }
    with _use(conn) as c:
        c.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade["id"], user_id, trade["ticker"], trade["side"], trade["quantity"], trade["price"], trade["executed_at"]),
        )
    return trade


def list_trades(
    limit: int | None = None, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    """Trades ascending by time. With ``limit``: the most recent N, still ascending."""
    sql = "SELECT id, ticker, side, quantity, price, executed_at FROM trades WHERE user_id = ?"
    with _use(conn) as c:
        rows = _ascending_tail(c, sql, (user_id,), "executed_at", limit)
    return [_trade(r) for r in rows]


def _ascending_tail(
    c: sqlite3.Connection, sql: str, params: tuple, time_col: str, limit: int | None
) -> list[sqlite3.Row]:
    if limit is None:
        return c.execute(f"{sql} ORDER BY {time_col} ASC, rowid ASC", params).fetchall()
    rows = c.execute(f"{sql} ORDER BY {time_col} DESC, rowid DESC LIMIT ?", (*params, int(limit))).fetchall()
    rows.reverse()
    return rows


# ---------------------------------------------------------------- watchlist

def get_watchlist(user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """[{ticker, added_at}] in insertion order."""
    with _use(conn) as c:
        rows = c.execute(
            "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at ASC, rowid ASC",
            (user_id,),
        ).fetchall()
    return [{"ticker": r["ticker"], "added_at": r["added_at"]} for r in rows]


def add_watchlist_ticker(
    ticker: str, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    """Add a ticker. Returns {ticker, added_at}, or None if it was already present."""
    t = _norm(ticker)
    ts = now_iso()
    with _use(conn) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (new_id(), user_id, t, ts),
        )
    return {"ticker": t, "added_at": ts} if cur.rowcount == 1 else None


def remove_watchlist_ticker(
    ticker: str, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None
) -> bool:
    """Returns True if the ticker was removed, False if it was not on the watchlist."""
    with _use(conn) as c:
        cur = c.execute("DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, _norm(ticker)))
    return cur.rowcount > 0


# ---------------------------------------------------------------- portfolio snapshots

def insert_snapshot(
    total_value: float,
    user_id: str = DEFAULT_USER_ID,
    conn: sqlite3.Connection | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    snap = {"total_value": float(total_value), "recorded_at": recorded_at or now_iso()}
    with _use(conn) as c:
        c.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) VALUES (?, ?, ?, ?)",
            (new_id(), user_id, snap["total_value"], snap["recorded_at"]),
        )
    return snap


def list_snapshots(
    limit: int | None = None, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    """[{total_value, recorded_at}] ascending. With ``limit``: the most recent N, still ascending."""
    sql = "SELECT total_value, recorded_at FROM portfolio_snapshots WHERE user_id = ?"
    with _use(conn) as c:
        rows = _ascending_tail(c, sql, (user_id,), "recorded_at", limit)
    return [{"total_value": float(r["total_value"]), "recorded_at": r["recorded_at"]} for r in rows]


# ---------------------------------------------------------------- chat messages

def _chat(row: sqlite3.Row) -> dict[str, Any]:
    actions = row["actions"]
    if actions is not None:
        try:
            actions = json.loads(actions)
        except (TypeError, ValueError):
            pass  # leave the raw string rather than fail history loading
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "actions": actions,
        "created_at": row["created_at"],
    }


def insert_chat_message(
    role: str,
    content: str,
    actions: Any = None,
    user_id: str = DEFAULT_USER_ID,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """``actions`` is any JSON-serialisable value (stored as JSON text) or None."""
    msg = {"id": new_id(), "role": role, "content": content, "actions": actions, "created_at": now_iso()}
    with _use(conn) as c:
        c.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                msg["id"],
                user_id,
                role,
                content,
                json.dumps(actions) if actions is not None else None,
                msg["created_at"],
            ),
        )
    return msg


def list_chat_messages(
    limit: int | None = 50, user_id: str = DEFAULT_USER_ID, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    """[{id, role, content, actions, created_at}] ascending; the most recent ``limit`` (None = all)."""
    sql = "SELECT id, role, content, actions, created_at FROM chat_messages WHERE user_id = ?"
    with _use(conn) as c:
        rows = _ascending_tail(c, sql, (user_id,), "created_at", limit)
    return [_chat(r) for r in rows]


# ---------------------------------------------------------------- atomic trade

class TradeValidationError(ValueError):
    """Raised by apply_trade when a trade fails validation. Nothing is written."""


class InsufficientCashError(TradeValidationError):
    pass


class InsufficientSharesError(TradeValidationError):
    pass


def apply_trade(
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """Validate and apply a market-order fill atomically (one BEGIN IMMEDIATE transaction).

    Updates cash, upserts/deletes the position (weighted-average cost on buys; avg cost
    unchanged on sells; row deleted when fully sold) and appends the trade row.

    Returns ``{"trade": {...insert_trade shape...}, "cash_balance": float,
    "position": {...get_position shape...} | None}``.
    Raises InsufficientCashError / InsufficientSharesError / TradeValidationError.
    """
    t = _norm(ticker)
    side = side.strip().lower()
    quantity = float(quantity)
    price = float(price)
    if side not in ("buy", "sell"):
        raise TradeValidationError(f"Invalid side '{side}'; must be 'buy' or 'sell'")
    if not quantity > 0:
        raise TradeValidationError("Quantity must be greater than 0")
    if not price > 0:
        raise TradeValidationError(f"No valid price available for {t}")

    with transaction() as c:
        cash = get_cash(user_id, conn=c)
        pos = get_position(t, user_id, conn=c)
        held = pos["quantity"] if pos else 0.0
        cost = quantity * price

        if side == "buy":
            if cost > cash + 1e-9:
                raise InsufficientCashError(
                    f"Insufficient cash: buying {quantity:g} {t} costs ${cost:,.2f}, available ${cash:,.2f}"
                )
            new_cash = cash - cost
            new_qty = held + quantity
            new_avg = ((held * pos["avg_cost"]) + cost) / new_qty if pos else price
        else:
            if quantity > held + QUANTITY_EPSILON:
                raise InsufficientSharesError(
                    f"Insufficient shares: trying to sell {quantity:g} {t}, holding {held:g}"
                )
            new_cash = cash + cost
            new_qty = held - quantity
            new_avg = pos["avg_cost"]

        set_cash(new_cash, user_id, conn=c)
        if new_qty <= QUANTITY_EPSILON:
            delete_position(t, user_id, conn=c)
            position = None
        else:
            position = upsert_position(t, new_qty, new_avg, user_id, conn=c)
        trade = insert_trade(t, side, quantity, price, user_id, conn=c)

    return {"trade": trade, "cash_balance": new_cash, "position": position}
