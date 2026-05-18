import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("BOT_DB_PATH", "bot.sqlite3")


def _table_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_rolls_table(conn) -> None:
    cols = _table_columns(conn, "rolls")
    if not cols or "tiebreak_round_no" not in cols:
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        """
        DROP TABLE IF EXISTS rolls_new;

        CREATE TABLE IF NOT EXISTS rolls_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competition_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            display_name TEXT NOT NULL,
            value INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(competition_id, user_id),
            FOREIGN KEY(competition_id) REFERENCES item_competitions(id)
        );

        INSERT OR REPLACE INTO rolls_new (id, competition_id, user_id, username, display_name, value, created_at)
        SELECT id, competition_id, user_id, username, display_name, value, created_at
        FROM rolls
        ORDER BY id ASC;

        DROP TABLE rolls;
        ALTER TABLE rolls_new RENAME TO rolls;
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")


def _migrate_item_competitions_table(conn) -> None:
    cols = _table_columns(conn, "item_competitions")
    if not cols or "tiebreak_user_ids" not in cols:
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        """
        DROP TABLE IF EXISTS item_competitions_new;

        CREATE TABLE IF NOT EXISTS item_competitions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            round_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            winner_user_id INTEGER,
            winner_username TEXT,
            called_by_user_id INTEGER,
            called_by_username TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            called_at TEXT,
            UNIQUE(item_id, round_no),
            FOREIGN KEY(item_id) REFERENCES items(id)
        );

        INSERT OR REPLACE INTO item_competitions_new (
            id, item_id, round_no, status, winner_user_id, winner_username,
            called_by_user_id, called_by_username, created_at, called_at
        )
        SELECT
            id, item_id, round_no, status, winner_user_id, winner_username,
            called_by_user_id, called_by_username, created_at, called_at
        FROM item_competitions
        ORDER BY id ASC;

        DROP TABLE item_competitions;
        ALTER TABLE item_competitions_new RENAME TO item_competitions;
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS weeks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_key TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(week_id, code),
                FOREIGN KEY(week_id) REFERENCES weeks(id)
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_by_user_id INTEGER,
                created_by_username TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(slot_id) REFERENCES slots(id)
            );

            CREATE TABLE IF NOT EXISTS item_competitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                round_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                winner_user_id INTEGER,
                winner_username TEXT,
                called_by_user_id INTEGER,
                called_by_username TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                called_at TEXT,
                UNIQUE(item_id, round_no),
                FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS rolls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competition_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT NOT NULL,
                value INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(competition_id, user_id),
                FOREIGN KEY(competition_id) REFERENCES item_competitions(id)
            );

            CREATE TABLE IF NOT EXISTS bot_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id INTEGER,
                message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(week_id, kind),
                FOREIGN KEY(week_id) REFERENCES weeks(id)
            );

            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_id INTEGER,
                user_id INTEGER,
                username TEXT,
                display_name TEXT,
                action TEXT NOT NULL,
                slot_code TEXT,
                item_id INTEGER,
                item_name TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(week_id) REFERENCES weeks(id)
            );
            """
        )
        _migrate_rolls_table(conn)
        _migrate_item_competitions_table(conn)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
