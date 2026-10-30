"""SQLite persistence for accounts, groups, channels and message history."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class Store:
    """Small synchronous store owned by the server hub.

    SQLite operations are short and happen on the asyncio thread.  This keeps
    the current protocol code simple; the database is local to the process.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rooms (
                room_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                owner TEXT,
                group_id TEXT,
                invite_token TEXT,
                state_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS group_members (
                group_id TEXT NOT NULL,
                account TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at INTEGER NOT NULL,
                PRIMARY KEY(group_id, account)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                room_key TEXT,
                sender TEXT NOT NULL,
                recipient TEXT,
                text TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'text',
                attachment_id TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_room
                ON messages(scope, room_key, id);
            CREATE INDEX IF NOT EXISTS idx_messages_private
                ON messages(scope, sender, recipient, id);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ accounts ----

    def seed_accounts(self, accounts: dict[str, str]) -> None:
        for account, password in accounts.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO accounts(account, password, created_at) VALUES (?, ?, ?)",
                (account, password, int(time.time() * 1000)),
            )
        self.conn.commit()

    def load_accounts(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT account, password FROM accounts").fetchall()
        return {row["account"]: row["password"] for row in rows}

    def save_account(self, account: str, password: str) -> None:
        self.conn.execute(
            """
            INSERT INTO accounts(account, password, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account) DO UPDATE SET password=excluded.password
            """,
            (account, password, int(time.time() * 1000)),
        )
        self.conn.commit()

    # --------------------------------------------------------------- rooms ----

    def load_rooms(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM rooms ORDER BY created_at, room_key"
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["state"] = json.loads(item.pop("state_json") or "{}")
            except json.JSONDecodeError:
                item["state"] = {}
            items.append(item)
        return items

    def save_room(
        self,
        room_key: str,
        display_name: str,
        kind: str,
        owner: str | None,
        group_id: str | None,
        invite_token: str | None,
        state: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO rooms(
                room_key, display_name, kind, owner, group_id,
                invite_token, state_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(room_key) DO UPDATE SET
                display_name=excluded.display_name,
                kind=excluded.kind,
                owner=excluded.owner,
                group_id=excluded.group_id,
                invite_token=excluded.invite_token,
                state_json=excluded.state_json
            """,
            (
                room_key,
                display_name,
                kind,
                owner,
                group_id,
                invite_token,
                json.dumps(state, ensure_ascii=False),
                int(time.time() * 1000),
            ),
        )
        self.conn.commit()

    def delete_room(self, room_key: str, group_id: str | None = None) -> None:
        self.conn.execute("DELETE FROM rooms WHERE room_key=?", (room_key,))
        if group_id:
            self.conn.execute(
                "DELETE FROM group_members WHERE group_id=?",
                (group_id,),
            )
            self.conn.execute(
                "DELETE FROM messages WHERE scope='room' AND room_key=?",
                (room_key,),
            )
        self.conn.commit()

    def add_group_member(self, group_id: str, account: str, role: str = "member") -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO group_members(group_id, account, role, joined_at)
            VALUES (?, ?, ?, ?)
            """,
            (group_id, account, role, int(time.time() * 1000)),
        )
        self.conn.commit()

    def load_group_members(self) -> dict[str, set[str]]:
        rows = self.conn.execute(
            "SELECT group_id, account FROM group_members"
        ).fetchall()
        result: dict[str, set[str]] = {}
        for row in rows:
            result.setdefault(row["group_id"], set()).add(row["account"])
        return result

    # ------------------------------------------------------------ messages ----

    def add_message(
        self,
        scope: str,
        sender: str,
        text: str,
        *,
        room_key: str | None = None,
        recipient: str | None = None,
        kind: str = "text",
        attachment_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = int(time.time() * 1000)
        cursor = self.conn.execute(
            """
            INSERT INTO messages(
                scope, room_key, sender, recipient, text, kind, attachment_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope, room_key, sender, recipient, text,
                kind, attachment_id, created_at,
            ),
        )
        self.conn.commit()
        return {
            "message_id": cursor.lastrowid,
            "scope": scope,
            "room_key": room_key,
            "sender": sender,
            "recipient": recipient,
            "text": text,
            "kind": kind,
            "attachment_id": attachment_id,
            "created_at": created_at,
        }

    def room_messages(
        self,
        room_key: str,
        before_id: int | None = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        limit = max(1, min(limit, 100))
        sql = (
            "SELECT * FROM messages WHERE scope='room' AND room_key=? "
        )
        args: list[Any] = [room_key]
        if before_id is not None:
            sql += "AND id < ? "
            args.append(before_id)
        sql += "ORDER BY id DESC LIMIT ?"
        args.append(limit + 1)
        rows = [dict(row) for row in self.conn.execute(sql, args).fetchall()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        next_before = rows[0]["id"] if has_more and rows else None
        return rows, has_more, next_before

    def private_messages(
        self,
        account: str,
        peer: str,
        before_id: int | None = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        limit = max(1, min(limit, 100))
        sql = """
            SELECT * FROM messages
            WHERE scope='private'
              AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))
        """
        args: list[Any] = [account, peer, peer, account]
        if before_id is not None:
            sql += " AND id < ?"
            args.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit + 1)
        rows = [dict(row) for row in self.conn.execute(sql, args).fetchall()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        next_before = rows[0]["id"] if has_more and rows else None
        return rows, has_more, next_before

    def private_conversations(self, account: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, sender, recipient, text, created_at
            FROM messages
            WHERE scope='private' AND (sender=? OR recipient=?)
            ORDER BY id DESC
            """,
            (account, account),
        ).fetchall()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            peer = row["recipient"] if row["sender"] == account else row["sender"]
            if peer in seen:
                continue
            seen.add(peer)
            result.append({
                "peer": peer,
                "count": 1,
                "last_text": row["text"],
                "last_from": row["sender"],
                "last_ts_ms": row["created_at"],
            })
        for item in result:
            item["count"] = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM messages
                WHERE scope='private'
                  AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))
                """,
                (account, item["peer"], item["peer"], account),
            ).fetchone()["n"]
        return result
