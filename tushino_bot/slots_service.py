import datetime as dt
import json
import random
from typing import Any

from db import get_conn

SLOT_CODES = ["пт1", "пт2", "сб1", "сб2"]


class SlotsError(Exception):
    pass


class NotFoundError(SlotsError):
    pass


class ConflictError(SlotsError):
    pass


class ValidationError(SlotsError):
    pass


class PermissionError(SlotsError):
    pass


def week_key_for_date(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row) if row is not None else None


def log_action(action: str, user: dict[str, Any] | None = None, week_id: int | None = None, slot_code: str | None = None, item_id: int | None = None, item_name: str | None = None, details: str | None = None) -> None:
    with get_conn() as conn:
        if week_id is None:
            week = conn.execute("SELECT * FROM weeks WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()
            week_id = week["id"] if week else None
        conn.execute(
            """
            INSERT INTO action_logs (week_id, user_id, username, display_name, action, slot_code, item_id, item_name, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                week_id,
                user.get("user_id") if user else None,
                user.get("username") if user else None,
                user.get("display_name") if user else None,
                action,
                slot_code,
                item_id,
                item_name,
                details,
            ),
        )
        conn.commit()


def get_action_logs(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM action_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_dict(row) for row in rows]


def create_or_get_active_week(target_week_key: str | None = None, force_new: bool = False) -> dict[str, Any]:
    target_week_key = target_week_key or week_key_for_date()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM weeks WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if existing and not force_new and existing["week_key"] == target_week_key:
            _ensure_slots(conn, existing["id"])
            conn.commit()
            return get_active_week(conn)

        current = conn.execute(
            "SELECT * FROM weeks WHERE week_key = ? ORDER BY id DESC LIMIT 1", (target_week_key,)
        ).fetchone()

        conn.execute("UPDATE weeks SET active = 0 WHERE active = 1")

        if current is None:
            cur = conn.execute(
                "INSERT INTO weeks (week_key, active) VALUES (?, 1)", (target_week_key,)
            )
            week_id = cur.lastrowid
        else:
            if force_new:
                _clear_week_data(conn, current["id"])
            conn.execute("UPDATE weeks SET active = 1 WHERE id = ?", (current["id"],))
            week_id = current["id"]

        _ensure_slots(conn, week_id)
        conn.commit()
        return get_active_week(conn)


def reset_active_week(target_week_key: str | None = None) -> dict[str, Any]:
    return create_or_get_active_week(target_week_key=target_week_key, force_new=True)


def get_active_week(conn=None) -> dict[str, Any]:
    owns_conn = conn is None
    if owns_conn:
        cm = get_conn()
        conn = cm.__enter__()
    try:
        week = conn.execute(
            "SELECT * FROM weeks WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if week is None:
            raise NotFoundError("Active week not found")
        return _build_week_payload(conn, week["id"])
    finally:
        if owns_conn:
            cm.__exit__(None, None, None)


def _clear_week_data(conn, week_id: int) -> None:
    conn.execute(
        "DELETE FROM rolls WHERE competition_id IN (SELECT c.id FROM item_competitions c JOIN items i ON i.id = c.item_id JOIN slots s ON s.id = i.slot_id WHERE s.week_id = ?)",
        (week_id,),
    )
    conn.execute(
        "DELETE FROM item_competitions WHERE item_id IN (SELECT i.id FROM items i JOIN slots s ON s.id = i.slot_id WHERE s.week_id = ?)",
        (week_id,),
    )
    conn.execute(
        "DELETE FROM items WHERE slot_id IN (SELECT id FROM slots WHERE week_id = ?)",
        (week_id,),
    )
    conn.execute("DELETE FROM slots WHERE week_id = ?", (week_id,))
    conn.execute("DELETE FROM bot_messages WHERE week_id = ?", (week_id,))


def _ensure_slots(conn, week_id: int) -> None:
    for code in SLOT_CODES:
        conn.execute(
            "INSERT OR IGNORE INTO slots (week_id, code) VALUES (?, ?)",
            (week_id, code),
        )


def _get_slot_row(conn, slot_code: str):
    row = conn.execute(
        """
        SELECT s.*
        FROM slots s
        JOIN weeks w ON w.id = s.week_id
        WHERE w.active = 1 AND s.code = ?
        ORDER BY s.id DESC LIMIT 1
        """,
        (slot_code,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Slot not found: {slot_code}")
    return row


def get_slot(slot_code: str) -> dict[str, Any]:
    with get_conn() as conn:
        slot = _get_slot_row(conn, slot_code)
        return _build_slot_payload(conn, slot)


def add_item(slot_code: str, item_name: str, user: dict[str, Any]) -> dict[str, Any]:
    item_name = (item_name or "").strip()
    if not item_name:
        raise ValidationError("Item name empty")
    with get_conn() as conn:
        slot = _get_slot_row(conn, slot_code)
        dup = conn.execute(
            "SELECT 1 FROM items WHERE slot_id = ? AND active = 1 AND lower(name) = lower(?)",
            (slot["id"], item_name),
        ).fetchone()
        if dup:
            raise ConflictError("Item already exists")

        cur = conn.execute(
            """
            INSERT INTO items (slot_id, name, created_by_user_id, created_by_username)
            VALUES (?, ?, ?, ?)
            """,
            (slot["id"], item_name, user.get("user_id"), user.get("username")),
        )
        conn.commit()
        result = get_item(cur.lastrowid)
        log_action("add_item", user=user, week_id=slot["week_id"], slot_code=slot_code, item_id=result["id"], item_name=item_name)
        return result


def delete_item(item_id: int, user: dict[str, Any], admin_user_ids: set[int] | None = None) -> None:
    admin_user_ids = admin_user_ids or set()
    with get_conn() as conn:
        item = _get_item_row(conn, item_id)
        creator_id = item["created_by_user_id"]
        if user.get("user_id") not in admin_user_ids and creator_id not in (None, user.get("user_id")):
            raise PermissionError("Only creator or admin can delete item")
        conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
        conn.commit()
        log_action("delete_item", user=user, week_id=item["week_id"], slot_code=item["slot_code"], item_id=item_id, item_name=item["name"])


def get_item(item_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        item = _get_item_row(conn, item_id)
        return _build_item_payload(conn, item)


def _get_item_row(conn, item_id: int):
    item = conn.execute(
        """
        SELECT i.*, s.code AS slot_code, s.week_id AS week_id
        FROM items i
        JOIN slots s ON s.id = i.slot_id
        JOIN weeks w ON w.id = s.week_id
        WHERE i.id = ? AND i.active = 1 AND w.active = 1
        """,
        (item_id,),
    ).fetchone()
    if item is None:
        raise NotFoundError(f"Item not found: {item_id}")
    return item


def _get_open_competition(conn, item_id: int):
    return conn.execute(
        """
        SELECT * FROM item_competitions
        WHERE item_id = ? AND status IN ('open', 'tiebreak')
        ORDER BY round_no DESC LIMIT 1
        """,
        (item_id,),
    ).fetchone()


def _create_competition(conn, item_id: int) -> int:
    last_round = conn.execute(
        "SELECT COALESCE(MAX(round_no), 0) AS round_no FROM item_competitions WHERE item_id = ?",
        (item_id,),
    ).fetchone()["round_no"]
    cur = conn.execute(
        "INSERT INTO item_competitions (item_id, round_no, status) VALUES (?, ?, 'open')",
        (item_id, last_round + 1),
    )
    return cur.lastrowid


def roll_for_item(item_id: int, user: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        item = _get_item_row(conn, item_id)
        comp = _get_open_competition(conn, item_id)
        if comp is None:
            comp_id = _create_competition(conn, item_id)
            comp = conn.execute("SELECT * FROM item_competitions WHERE id = ?", (comp_id,)).fetchone()

        tiebreak_round_no = 0
        allowed_user_ids = set()
        if comp["status"] == "tiebreak":
            tiebreak_round_no = 1
            allowed_user_ids = set(json.loads(comp["tiebreak_user_ids"] or "[]"))
            if user["user_id"] not in allowed_user_ids:
                raise ConflictError("Only tied users can roll now")

        existing = conn.execute(
            """
            SELECT 1 FROM rolls
            WHERE competition_id = ? AND user_id = ? AND tiebreak_round_no = ?
            """,
            (comp["id"], user["user_id"], tiebreak_round_no),
        ).fetchone()
        if existing:
            raise ConflictError("User already rolled for this item")

        value = random.randint(1, 6)
        conn.execute(
            """
            INSERT INTO rolls (competition_id, user_id, username, display_name, value, tiebreak_round_no)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                comp["id"],
                user["user_id"],
                user.get("username"),
                user.get("display_name") or user.get("username") or str(user["user_id"]),
                value,
                tiebreak_round_no,
            ),
        )
        conn.commit()
        payload = _build_item_payload(conn, item)
        payload["last_roll"] = {
            "value": value,
            "user_id": user["user_id"],
            "username": user.get("username"),
            "display_name": user.get("display_name"),
        }
        log_action("roll", user=user, week_id=item["week_id"], slot_code=item["slot_code"], item_id=item_id, item_name=item["name"], details=f"value={value};tiebreak_round_no={tiebreak_round_no}")
        return payload


def call_item(item_id: int, user: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        item = _get_item_row(conn, item_id)
        comp = _get_open_competition(conn, item_id)
        if comp is None:
            raise ConflictError("No open competition for item")

        tiebreak_round_no = 1 if comp["status"] == "tiebreak" else 0
        rolls = conn.execute(
            """
            SELECT * FROM rolls
            WHERE competition_id = ? AND tiebreak_round_no = ?
            ORDER BY value DESC, created_at ASC
            """,
            (comp["id"], tiebreak_round_no),
        ).fetchall()
        if not rolls:
            raise ConflictError("No rolls yet")

        top_value = rolls[0]["value"]
        tied = [r for r in rolls if r["value"] == top_value]
        if len(tied) > 1:
            conn.execute(
                "UPDATE item_competitions SET status = 'tiebreak', tiebreak_user_ids = ? WHERE id = ?",
                (json.dumps([r["user_id"] for r in tied]), comp["id"]),
            )
            conn.commit()
            payload = _build_item_payload(conn, item)
            payload["call_result"] = "tiebreak"
            log_action("call_tiebreak", user=user, week_id=item["week_id"], slot_code=item["slot_code"], item_id=item_id, item_name=item["name"], details=f"top_value={top_value}")
            payload["tied_users"] = [
                {
                    "user_id": r["user_id"],
                    "username": r["username"],
                    "display_name": r["display_name"],
                    "value": r["value"],
                }
                for r in tied
            ]
            return payload

        winner = rolls[0]
        conn.execute(
            """
            UPDATE item_competitions
            SET status = 'called',
                winner_user_id = ?,
                winner_username = ?,
                called_by_user_id = ?,
                called_by_username = ?,
                called_at = CURRENT_TIMESTAMP,
                tiebreak_user_ids = NULL
            WHERE id = ?
            """,
            (
                winner["user_id"],
                winner["username"],
                user.get("user_id"),
                user.get("username"),
                comp["id"],
            ),
        )
        conn.commit()
        payload = _build_item_payload(conn, item)
        payload["call_result"] = "winner"
        log_action("call_winner", user=user, week_id=item["week_id"], slot_code=item["slot_code"], item_id=item_id, item_name=item["name"], details=f"winner_user_id={winner['user_id']};value={winner['value']}")
        return payload


def reopen_item(item_id: int, user: dict[str, Any] | None = None) -> dict[str, Any]:
    with get_conn() as conn:
        item = _get_item_row(conn, item_id)
        open_comp = _get_open_competition(conn, item_id)
        if open_comp is not None:
            raise ConflictError("Competition already open")
        _create_competition(conn, item_id)
        conn.commit()
        result = _build_item_payload(conn, item)
        log_action("reopen_item", user=user, week_id=item["week_id"], slot_code=item["slot_code"], item_id=item_id, item_name=item["name"])
        return result


def undo_last_roll(user: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT r.*, c.status AS competition_status, c.item_id, i.name AS item_name, s.code AS slot_code, s.week_id
            FROM rolls r
            JOIN item_competitions c ON c.id = r.competition_id
            JOIN items i ON i.id = c.item_id
            JOIN slots s ON s.id = i.slot_id
            JOIN weeks w ON w.id = s.week_id
            WHERE w.active = 1 AND r.user_id = ? AND i.active = 1
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT 1
            """,
            (user["user_id"],),
        ).fetchone()
        if row is None:
            raise NotFoundError("No roll to undo")
        if row["competition_status"] == "called":
            raise ConflictError("Cannot undo roll after winner already called")

        conn.execute("DELETE FROM rolls WHERE id = ?", (row["id"],))
        conn.execute(
            "UPDATE item_competitions SET status = 'open', tiebreak_user_ids = NULL WHERE id = ?",
            (row["competition_id"],),
        )
        conn.commit()

        item = _get_item_row(conn, row["item_id"])
        payload = _build_item_payload(conn, item)
        log_action(
            "undo_roll",
            user=user,
            week_id=row["week_id"],
            slot_code=row["slot_code"],
            item_id=row["item_id"],
            item_name=row["item_name"],
            details=f"deleted_roll_id={row['id']};value={row['value']};tiebreak_round_no={row['tiebreak_round_no']}",
        )
        return payload


def save_bot_message(week_id: int, kind: str, chat_id: str, thread_id: int | None, message_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_messages (week_id, kind, chat_id, thread_id, message_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(week_id, kind)
            DO UPDATE SET chat_id = excluded.chat_id, thread_id = excluded.thread_id, message_id = excluded.message_id
            """,
            (week_id, kind, str(chat_id), thread_id, message_id),
        )
        conn.commit()


def get_bot_message(week_id: int, kind: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM bot_messages WHERE week_id = ? AND kind = ?",
            (week_id, kind),
        ).fetchone()
        return _row_to_dict(row)


def save_control_message(week_id: int, chat_id: str, thread_id: int | None, message_id: int) -> None:
    save_bot_message(week_id, 'week_control', chat_id, thread_id, message_id)


def get_control_message(week_id: int) -> dict[str, Any] | None:
    return get_bot_message(week_id, 'week_control')


def _build_week_payload(conn, week_id: int) -> dict[str, Any]:
    week = conn.execute("SELECT * FROM weeks WHERE id = ?", (week_id,)).fetchone()
    slots = conn.execute("SELECT * FROM slots WHERE week_id = ? ORDER BY id ASC", (week_id,)).fetchall()
    return {
        "id": week["id"],
        "week_key": week["week_key"],
        "active": bool(week["active"]),
        "slots": [_build_slot_payload(conn, slot) for slot in slots],
    }


def _build_slot_payload(conn, slot) -> dict[str, Any]:
    items = conn.execute(
        "SELECT * FROM items WHERE slot_id = ? AND active = 1 ORDER BY id ASC",
        (slot["id"],),
    ).fetchall()
    return {
        "id": slot["id"],
        "code": slot["code"],
        "items": [_build_item_payload(conn, item) for item in items],
    }


def _build_item_payload(conn, item) -> dict[str, Any]:
    competitions = conn.execute(
        "SELECT * FROM item_competitions WHERE item_id = ? ORDER BY round_no DESC",
        (item["id"],),
    ).fetchall()
    current = competitions[0] if competitions else None
    score_rows = []
    latest_roll = None
    if current is not None:
        score_rows = conn.execute(
            """
            SELECT user_id, username, display_name,
                   MAX(value) AS best_value,
                   MAX(CASE WHEN tiebreak_round_no = 0 THEN value END) AS base_value,
                   MAX(CASE WHEN tiebreak_round_no = 1 THEN value END) AS tiebreak_value
            FROM rolls
            WHERE competition_id = ?
            GROUP BY user_id, username, display_name
            """,
            (current["id"],),
        ).fetchall()
        latest_roll = conn.execute(
            """
            SELECT user_id, username, display_name, value, tiebreak_round_no
            FROM rolls
            WHERE competition_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (current["id"],),
        ).fetchone()

    has_tiebreak_scores = any(row["tiebreak_value"] is not None for row in score_rows)
    sorted_scores = sorted(
        score_rows,
        key=lambda row: (
            row["tiebreak_value"] if has_tiebreak_scores and row["tiebreak_value"] is not None else row["base_value"] or 0,
            row["base_value"] or 0,
            -row["user_id"],
        ),
        reverse=True,
    )
    tied_user_ids = json.loads(current["tiebreak_user_ids"] or "[]") if current and current["tiebreak_user_ids"] else []
    by_user_id = {row["user_id"]: row for row in sorted_scores}
    tied_display_names = [
        (by_user_id[user_id]["display_name"] or by_user_id[user_id]["username"])
        for user_id in tied_user_ids
        if user_id in by_user_id
    ]

    return {
        "id": item["id"],
        "slot_id": item["slot_id"],
        "slot_code": item["slot_code"] if "slot_code" in item.keys() else None,
        "name": item["name"],
        "created_by_user_id": item["created_by_user_id"],
        "created_by_username": item["created_by_username"],
        "status": current["status"] if current else "open",
        "round_no": current["round_no"] if current else 1,
        "winner_user_id": current["winner_user_id"] if current else None,
        "winner_username": current["winner_username"] if current else None,
        "tied_user_ids": tied_user_ids,
        "tied_display_names": tied_display_names,
        "scores": [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "best_value": row["tiebreak_value"] if has_tiebreak_scores and row["tiebreak_value"] is not None else row["best_value"],
                "base_value": row["base_value"],
                "tiebreak_value": row["tiebreak_value"],
            }
            for row in sorted_scores
        ],
        "latest_roll": {
            "user_id": latest_roll["user_id"],
            "username": latest_roll["username"],
            "display_name": latest_roll["display_name"],
            "value": latest_roll["value"],
            "tiebreak_round_no": latest_roll["tiebreak_round_no"],
        } if latest_roll else None,
    }
