from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import GroupMemberDecrease, GroupMemberIncrease, JoinRequest


APPLICATION_MATCH_WINDOW_SECONDS = 30 * 24 * 60 * 60


def _event_hash(*parts: Any) -> str:
    payload = "\x00".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditStore:
    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    def _configure(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            if self.database != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")

    def _migrate(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_key TEXT NOT NULL UNIQUE,
            platform_id TEXT NOT NULL,
            self_id TEXT NOT NULL DEFAULT '',
            group_id TEXT NOT NULL,
            applicant_qq TEXT NOT NULL,
            requested_at INTEGER NOT NULL,
            observed_at INTEGER NOT NULL,
            nickname TEXT NOT NULL DEFAULT '',
            question TEXT NOT NULL DEFAULT '',
            question_source TEXT NOT NULL DEFAULT 'unknown',
            answer TEXT NOT NULL DEFAULT '',
            raw_comment TEXT NOT NULL DEFAULT '',
            review_prompt TEXT NOT NULL DEFAULT '',
            external_checked_at INTEGER,
            external_actor_qq TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS application_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            action TEXT NOT NULL,
            actor_qq TEXT NOT NULL DEFAULT '',
            actor_name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            occurred_at INTEGER NOT NULL,
            observed_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS membership_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            join_event_key TEXT UNIQUE,
            leave_event_key TEXT UNIQUE,
            application_id INTEGER REFERENCES applications(id) ON DELETE SET NULL,
            platform_id TEXT NOT NULL,
            self_id TEXT NOT NULL DEFAULT '',
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            joined_at INTEGER,
            join_sub_type TEXT NOT NULL DEFAULT '',
            join_operator_qq TEXT NOT NULL DEFAULT '',
            nickname_at_join TEXT NOT NULL DEFAULT '',
            card_at_join TEXT NOT NULL DEFAULT '',
            left_at INTEGER,
            leave_sub_type TEXT NOT NULL DEFAULT '',
            leave_operator_qq TEXT NOT NULL DEFAULT '',
            correlation TEXT NOT NULL DEFAULT 'none',
            observed_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS card_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            membership_id INTEGER NOT NULL REFERENCES membership_sessions(id) ON DELETE CASCADE,
            template TEXT NOT NULL,
            old_card TEXT NOT NULL DEFAULT '',
            target_card TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            attempted_at INTEGER NOT NULL,
            completed_at INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_applications_lookup
            ON applications(platform_id, group_id, applicant_qq, requested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_actions_application
            ON application_actions(application_id, occurred_at, id);
        CREATE INDEX IF NOT EXISTS idx_memberships_lookup
            ON membership_sessions(platform_id, group_id, user_id, joined_at DESC);
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)
            self._connection.execute("PRAGMA user_version = 1")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def request_key(platform_id: str, self_id: str, flag: str) -> str:
        return _event_hash("request", platform_id, flag)

    def record_application(
        self,
        *,
        platform_id: str,
        request: JoinRequest,
        question: str,
        question_source: str,
        review_prompt: str,
        observed_at: int | None = None,
    ) -> tuple[int, bool]:
        observed_at = observed_at or int(time.time())
        requested_at = request.requested_at or observed_at
        key = self.request_key(platform_id, request.self_id, request.flag)
        values = (
            key,
            platform_id,
            request.self_id,
            request.group_id,
            request.applicant_qq,
            requested_at,
            observed_at,
            request.nickname,
            question,
            question_source,
            request.answer,
            request.raw_comment or request.answer,
            review_prompt,
        )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO applications (
                    request_key, platform_id, self_id, group_id, applicant_qq,
                    requested_at, observed_at, nickname, question, question_source,
                    answer, raw_comment, review_prompt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            created = cursor.rowcount == 1
            row = self._connection.execute(
                "SELECT id FROM applications WHERE request_key = ?",
                (key,),
            ).fetchone()
        if row is None:  # pragma: no cover - guarded by the transaction above.
            raise RuntimeError("failed to persist join application")
        return int(row["id"]), created

    def record_action(
        self,
        *,
        application_id: int,
        kind: str,
        action: str,
        actor_qq: str,
        source: str,
        status: str,
        reason: str = "",
        occurred_at: int | None = None,
        actor_name: str = "",
        event_key: str | None = None,
    ) -> bool:
        occurred_at = occurred_at or int(time.time())
        event_key = event_key or _event_hash(
            "action",
            application_id,
            kind,
            action,
            actor_qq,
            source,
            status,
        )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO application_actions (
                    event_key, application_id, kind, action, actor_qq, actor_name,
                    source, status, reason, occurred_at, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    application_id,
                    kind,
                    action,
                    actor_qq,
                    actor_name,
                    source,
                    status,
                    reason,
                    occurred_at,
                    int(time.time()),
                ),
            )
            return cursor.rowcount == 1

    def _match_application(
        self,
        *,
        platform_id: str,
        group_id: str,
        user_id: str,
        joined_at: int,
    ) -> tuple[int | None, str]:
        row = self._connection.execute(
            """
            SELECT a.id,
                   EXISTS(
                       SELECT 1 FROM application_actions aa
                       WHERE aa.application_id = a.id
                         AND aa.kind = 'platform'
                         AND aa.action = 'approve'
                         AND aa.status = 'succeeded'
                   ) AS plugin_approved
            FROM applications a
            WHERE a.platform_id = ? AND a.group_id = ? AND a.applicant_qq = ?
              AND a.requested_at <= ?
              AND a.requested_at >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM membership_sessions ms WHERE ms.application_id = a.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM application_actions aa
                  WHERE aa.application_id = a.id
                    AND aa.kind = 'platform'
                    AND aa.action = 'reject'
                    AND aa.status = 'succeeded'
              )
            ORDER BY a.requested_at DESC, a.id DESC
            LIMIT 1
            """,
            (
                platform_id,
                group_id,
                user_id,
                joined_at + 60,
                joined_at - APPLICATION_MATCH_WINDOW_SECONDS,
            ),
        ).fetchone()
        if row is None:
            return None, "none"
        return int(row["id"]), "plugin_action" if row["plugin_approved"] else "latest_request"

    def record_join(
        self,
        *,
        platform_id: str,
        event: GroupMemberIncrease,
        nickname: str = "",
        old_card: str = "",
        application_id_hint: int | None = None,
        correlation_hint: str = "",
    ) -> tuple[int, int | None, bool]:
        join_key = _event_hash(
            "join",
            platform_id,
            event.self_id,
            event.group_id,
            event.user_id,
            event.operator_id,
            event.sub_type,
            event.occurred_at,
        )
        with self._lock, self._connection:
            if application_id_hint is not None:
                hinted = self._connection.execute(
                    """
                    SELECT id FROM applications
                    WHERE id = ? AND platform_id = ? AND group_id = ? AND applicant_qq = ?
                    """,
                    (
                        application_id_hint,
                        platform_id,
                        event.group_id,
                        event.user_id,
                    ),
                ).fetchone()
                if hinted is None:
                    raise ValueError("application hint does not match the joining member")

            existing = self._connection.execute(
                "SELECT id, application_id FROM membership_sessions WHERE join_event_key = ?",
                (join_key,),
            ).fetchone()
            if existing is not None:
                application_id = existing["application_id"]
                if application_id is None and application_id_hint is not None:
                    application_id = application_id_hint
                    self._connection.execute(
                        """
                        UPDATE membership_sessions
                        SET application_id = ?,
                            nickname_at_join = CASE
                                WHEN nickname_at_join = '' THEN ? ELSE nickname_at_join
                            END,
                            card_at_join = CASE
                                WHEN card_at_join = '' THEN ? ELSE card_at_join
                            END
                        WHERE id = ?
                        """,
                        (
                            application_id_hint,
                            nickname,
                            old_card,
                            int(existing["id"]),
                        ),
                    )
                return int(existing["id"]), int(application_id) if application_id else None, False

            if correlation_hint == "group_increase":
                provisional = self._connection.execute(
                    """
                    SELECT id, application_id FROM membership_sessions
                    WHERE platform_id = ? AND group_id = ? AND user_id = ?
                      AND left_at IS NULL
                      AND correlation IN ('member_reconcile_direct', 'card_backfill_direct')
                      AND joined_at BETWEEN ? AND ?
                    ORDER BY joined_at DESC, id DESC LIMIT 1
                    """,
                    (
                        platform_id,
                        event.group_id,
                        event.user_id,
                        event.occurred_at - 60,
                        event.occurred_at + 60,
                    ),
                ).fetchone()
                if provisional is not None:
                    self._connection.execute(
                        """
                        UPDATE membership_sessions
                        SET join_event_key = ?, self_id = ?, joined_at = ?,
                            join_sub_type = ?, join_operator_qq = ?,
                            nickname_at_join = CASE
                                WHEN ? != '' THEN ? ELSE nickname_at_join
                            END,
                            card_at_join = CASE
                                WHEN ? != '' THEN ? ELSE card_at_join
                            END,
                            correlation = 'group_increase', observed_at = ?
                        WHERE id = ?
                        """,
                        (
                            join_key,
                            event.self_id,
                            event.occurred_at,
                            event.sub_type,
                            event.operator_id,
                            nickname,
                            nickname,
                            old_card,
                            old_card,
                            int(time.time()),
                            int(provisional["id"]),
                        ),
                    )
                    application_id = provisional["application_id"]
                    return (
                        int(provisional["id"]),
                        int(application_id) if application_id else None,
                        False,
                    )

            same_join = self._connection.execute(
                """
                SELECT id, application_id FROM membership_sessions
                WHERE platform_id = ? AND group_id = ? AND user_id = ?
                  AND joined_at = ? AND left_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (platform_id, event.group_id, event.user_id, event.occurred_at),
            ).fetchone()
            if same_join is not None:
                application_id = same_join["application_id"]
                if application_id is None and application_id_hint is not None:
                    application_id = application_id_hint
                self._connection.execute(
                    """
                    UPDATE membership_sessions
                    SET application_id = COALESCE(application_id, ?),
                        self_id = CASE WHEN self_id = '' THEN ? ELSE self_id END,
                        join_sub_type = CASE WHEN ? != '' THEN ? ELSE join_sub_type END,
                        join_operator_qq = CASE
                            WHEN ? != '' AND (
                                join_operator_qq = '' OR ? = 1 OR ? = 'group_increase'
                            )
                            THEN ? ELSE join_operator_qq
                        END,
                        correlation = CASE
                            WHEN ? = 1 OR ? = 'group_increase'
                            THEN 'group_increase' ELSE correlation
                        END,
                        nickname_at_join = CASE WHEN nickname_at_join = '' THEN ? ELSE nickname_at_join END,
                        card_at_join = CASE WHEN card_at_join = '' THEN ? ELSE card_at_join END
                    WHERE id = ?
                    """,
                    (
                        application_id_hint,
                        event.self_id,
                        event.sub_type,
                        event.sub_type,
                        event.operator_id,
                        int(application_id_hint is None),
                        correlation_hint,
                        event.operator_id,
                        int(application_id_hint is None),
                        correlation_hint,
                        nickname,
                        old_card,
                        int(same_join["id"]),
                    ),
                )
                return (
                    int(same_join["id"]),
                    int(application_id) if application_id else None,
                    False,
                )

            if application_id_hint is not None:
                application_id = application_id_hint
                correlation = correlation_hint or "confirmed_member"
            else:
                application_id, correlation = self._match_application(
                    platform_id=platform_id,
                    group_id=event.group_id,
                    user_id=event.user_id,
                    joined_at=event.occurred_at,
                )
            cursor = self._connection.execute(
                """
                INSERT INTO membership_sessions (
                    join_event_key, application_id, platform_id, self_id, group_id,
                    user_id, joined_at, join_sub_type, join_operator_qq,
                    nickname_at_join, card_at_join, correlation, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    join_key,
                    application_id,
                    platform_id,
                    event.self_id,
                    event.group_id,
                    event.user_id,
                    event.occurred_at,
                    event.sub_type,
                    event.operator_id,
                    nickname,
                    old_card,
                    correlation,
                    int(time.time()),
                ),
            )
            membership_id = int(cursor.lastrowid)
            if application_id and nickname:
                self._connection.execute(
                    "UPDATE applications SET nickname = ? WHERE id = ? AND nickname = ''",
                    (nickname, application_id),
                )
        return membership_id, application_id, True

    def application_for_reconciliation(self, application_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT a.*,
                       EXISTS(
                           SELECT 1 FROM application_actions approved
                           WHERE approved.application_id = a.id
                             AND approved.kind = 'platform'
                             AND approved.action = 'approve'
                             AND approved.status IN ('succeeded', 'observed')
                       ) AS explicitly_approved,
                       COALESCE(
                           (
                               SELECT aa.actor_qq FROM application_actions aa
                               WHERE aa.application_id = a.id
                                 AND aa.kind = 'platform' AND aa.action = 'approve'
                                 AND aa.status IN ('succeeded', 'observed')
                               ORDER BY aa.occurred_at DESC, aa.id DESC LIMIT 1
                           ),
                           NULLIF(a.external_actor_qq, ''),
                           a.self_id
                       ) AS approval_actor_qq,
                       COALESCE(
                           (
                               SELECT aa.occurred_at FROM application_actions aa
                               WHERE aa.application_id = a.id
                                 AND aa.kind = 'platform' AND aa.action = 'approve'
                                 AND aa.status IN ('succeeded', 'observed')
                               ORDER BY aa.occurred_at DESC, aa.id DESC LIMIT 1
                           ),
                           a.external_checked_at,
                           a.requested_at
                       ) AS approval_at,
                       (
                           SELECT ms.id FROM membership_sessions ms
                           WHERE ms.application_id = a.id AND ms.left_at IS NULL
                           ORDER BY ms.joined_at DESC, ms.id DESC LIMIT 1
                       ) AS membership_id,
                       (
                           SELECT ms.joined_at FROM membership_sessions ms
                           WHERE ms.application_id = a.id AND ms.left_at IS NULL
                           ORDER BY ms.joined_at DESC, ms.id DESC LIMIT 1
                       ) AS membership_joined_at
                FROM applications a WHERE a.id = ?
                """,
                (application_id,),
            ).fetchone()
        return dict(row) if row else None

    def pending_join_applications(
        self,
        *,
        platform_id: str,
        group_ids: list[str],
        now: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not group_ids:
            return []
        placeholders = ", ".join("?" for _ in group_ids)
        sql = f"""
            SELECT a.id FROM applications a
            WHERE a.platform_id = ? AND a.group_id IN ({placeholders})
              AND a.requested_at BETWEEN ? AND ?
              AND NOT EXISTS (
                  SELECT 1 FROM membership_sessions ms WHERE ms.application_id = a.id
              )
              AND (
                  a.external_checked_at IS NOT NULL
                  OR EXISTS (
                      SELECT 1 FROM application_actions aa
                      WHERE aa.application_id = a.id AND aa.kind = 'platform'
                        AND aa.action = 'approve'
                        AND aa.status IN ('succeeded', 'observed')
                  )
              )
              AND NOT EXISTS (
                  SELECT 1 FROM application_actions aa
                  WHERE aa.application_id = a.id AND aa.kind = 'platform'
                    AND aa.action = 'reject'
                    AND aa.status IN ('succeeded', 'inferred')
              )
            ORDER BY a.requested_at DESC, a.id DESC LIMIT ?
        """
        params: list[Any] = [platform_id, *group_ids]
        params.extend((now - APPLICATION_MATCH_WINDOW_SECONDS, now + 60, limit))
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
            applications = [
                self.application_for_reconciliation(int(row["id"])) for row in rows
            ]
        return [application for application in applications if application is not None]

    def card_candidate_user_ids(self, *, platform_id: str, group_id: str) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT applicant_qq FROM applications
                WHERE platform_id = ? AND group_id = ? AND applicant_qq != ''
                ORDER BY applicant_qq
                """,
                (platform_id, group_id),
            ).fetchall()
        return [str(row["applicant_qq"]) for row in rows]

    def card_backfill_applications(
        self,
        *,
        platform_id: str,
        group_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT a.id, a.applicant_qq,
                       EXISTS(
                           SELECT 1 FROM membership_sessions open_ms
                           WHERE open_ms.application_id = a.id
                             AND open_ms.left_at IS NULL
                       ) AS has_open_membership,
                       EXISTS(
                           SELECT 1 FROM membership_sessions any_ms
                           WHERE any_ms.application_id = a.id
                       ) AS has_membership,
                       EXISTS(
                           SELECT 1 FROM application_actions rejected
                           WHERE rejected.application_id = a.id
                             AND rejected.kind = 'platform'
                             AND rejected.action = 'reject'
                             AND rejected.status = 'succeeded'
                       ) AS explicitly_rejected,
                       (
                           a.external_checked_at IS NOT NULL
                           OR EXISTS (
                               SELECT 1 FROM application_actions approved
                               WHERE approved.application_id = a.id
                                 AND approved.kind = 'platform'
                                 AND approved.action = 'approve'
                                 AND approved.status IN ('succeeded', 'observed')
                           )
                       ) AS has_approval_evidence
                FROM applications a
                WHERE a.platform_id = ? AND a.group_id = ?
                  AND a.applicant_qq != ''
                ORDER BY a.applicant_qq,
                         has_open_membership DESC,
                         a.requested_at DESC,
                         a.id DESC
                """,
                (platform_id, group_id),
            ).fetchall()
            applications: list[dict[str, Any]] = []
            seen_users: set[str] = set()
            for row in rows:
                user_id = str(row["applicant_qq"])
                if user_id in seen_users:
                    continue
                seen_users.add(user_id)
                eligible = bool(row["has_open_membership"]) or (
                    not bool(row["has_membership"])
                    and not bool(row["explicitly_rejected"])
                    and bool(row["has_approval_evidence"])
                )
                if not eligible:
                    continue
                application = self.application_for_reconciliation(int(row["id"]))
                if application is not None:
                    applications.append(application)
        return applications

    def find_application_for_member(
        self,
        *,
        platform_id: str,
        group_id: str,
        user_id: str,
        joined_at: int,
        now: int | None = None,
    ) -> dict[str, Any] | None:
        upper_join_time = joined_at + 60 if joined_at > 0 else (now or int(time.time())) + 60
        lower_join_time = (
            joined_at - APPLICATION_MATCH_WINDOW_SECONDS if joined_at > 0 else 0
        )
        with self._lock:
            membership = self._connection.execute(
                """
                SELECT id, application_id FROM membership_sessions
                WHERE platform_id = ? AND group_id = ? AND user_id = ?
                  AND left_at IS NULL AND application_id IS NOT NULL
                  AND (? <= 0 OR joined_at <= 0 OR ABS(joined_at - ?) <= 60)
                ORDER BY joined_at DESC, id DESC LIMIT 1
                """,
                (platform_id, group_id, user_id, joined_at, joined_at),
            ).fetchone()
            if membership is not None:
                application = self.application_for_reconciliation(
                    int(membership["application_id"])
                )
                if application is not None:
                    application["membership_id"] = int(membership["id"])
                    return application

            row = self._connection.execute(
                """
                SELECT a.id FROM applications a
                WHERE a.platform_id = ? AND a.group_id = ? AND a.applicant_qq = ?
                  AND a.requested_at BETWEEN ? AND ?
                  AND NOT EXISTS (
                      SELECT 1 FROM membership_sessions ms WHERE ms.application_id = a.id
                  )
                  AND (
                      EXISTS (
                          SELECT 1 FROM application_actions aa
                          WHERE aa.application_id = a.id AND aa.kind = 'platform'
                            AND aa.action = 'approve'
                            AND aa.status IN ('succeeded', 'observed')
                      )
                      OR (? > 0 AND a.external_checked_at IS NOT NULL)
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM application_actions aa
                      WHERE aa.application_id = a.id AND aa.kind = 'platform'
                        AND aa.action = 'reject' AND aa.status = 'succeeded'
                  )
                ORDER BY a.requested_at DESC, a.id DESC LIMIT 1
                """,
                (
                    platform_id,
                    group_id,
                    user_id,
                    lower_join_time,
                    upper_join_time,
                    joined_at,
                ),
            ).fetchone()
            if row is not None:
                return self.application_for_reconciliation(int(row["id"]))

            fallback_rows = self._connection.execute(
                """
                SELECT a.id FROM applications a
                WHERE a.platform_id = ? AND a.group_id = ? AND a.applicant_qq = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM membership_sessions ms WHERE ms.application_id = a.id
                  )
                  AND (
                      a.external_checked_at IS NOT NULL
                      OR EXISTS (
                          SELECT 1 FROM application_actions aa
                          WHERE aa.application_id = a.id AND aa.kind = 'platform'
                            AND aa.action = 'approve'
                            AND aa.status IN ('succeeded', 'observed')
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM application_actions aa
                      WHERE aa.application_id = a.id AND aa.kind = 'platform'
                        AND aa.action = 'reject' AND aa.status = 'succeeded'
                  )
                ORDER BY a.requested_at DESC, a.id DESC LIMIT 2
                """,
                (platform_id, group_id, user_id),
            ).fetchall()
            if len(fallback_rows) != 1:
                return None
            application = self.application_for_reconciliation(
                int(fallback_rows[0]["id"])
            )
            if application is not None:
                application["time_correlation_fallback"] = "single_candidate"
            return application

    def has_successful_card_operation(self, membership_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM card_operations
                WHERE membership_id = ? AND status = 'succeeded' LIMIT 1
                """,
                (membership_id,),
            ).fetchone()
        return row is not None

    def update_application_answer(self, application_id: int, answer: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE applications SET answer = ? WHERE id = ?",
                (answer, application_id),
            )

    def update_application_nickname(self, application_id: int, nickname: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE applications SET nickname = ? WHERE id = ? AND nickname = ''",
                (nickname, application_id),
            )

    def record_leave(
        self,
        *,
        platform_id: str,
        event: GroupMemberDecrease,
    ) -> tuple[int, bool]:
        leave_key = _event_hash(
            "leave",
            platform_id,
            event.self_id,
            event.group_id,
            event.user_id,
            event.operator_id,
            event.sub_type,
            event.occurred_at,
        )
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT id FROM membership_sessions WHERE leave_event_key = ?",
                (leave_key,),
            ).fetchone()
            if existing is not None:
                return int(existing["id"]), False

            open_session = self._connection.execute(
                """
                SELECT id FROM membership_sessions
                WHERE platform_id = ? AND group_id = ? AND user_id = ? AND left_at IS NULL
                ORDER BY joined_at DESC, id DESC LIMIT 1
                """,
                (platform_id, event.group_id, event.user_id),
            ).fetchone()
            if open_session is not None:
                membership_id = int(open_session["id"])
                self._connection.execute(
                    """
                    UPDATE membership_sessions
                    SET leave_event_key = ?, left_at = ?, leave_sub_type = ?, leave_operator_qq = ?
                    WHERE id = ?
                    """,
                    (
                        leave_key,
                        event.occurred_at,
                        event.sub_type,
                        event.operator_id,
                        membership_id,
                    ),
                )
                return membership_id, True

            cursor = self._connection.execute(
                """
                INSERT INTO membership_sessions (
                    leave_event_key, platform_id, self_id, group_id, user_id,
                    left_at, leave_sub_type, leave_operator_qq, correlation, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unmatched_leave', ?)
                """,
                (
                    leave_key,
                    platform_id,
                    event.self_id,
                    event.group_id,
                    event.user_id,
                    event.occurred_at,
                    event.sub_type,
                    event.operator_id,
                    int(time.time()),
                ),
            )
            return int(cursor.lastrowid), True

    def record_card_operation(
        self,
        *,
        membership_id: int,
        template: str,
        old_card: str,
        target_card: str,
        status: str,
        error: str = "",
        attempted_at: int | None = None,
    ) -> bool:
        attempted_at = attempted_at or int(time.time())
        event_key = _event_hash("card", membership_id, template, target_card)
        completed_at = attempted_at if status in {"succeeded", "failed", "skipped"} else None
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR REPLACE INTO card_operations (
                    id, event_key, membership_id, template, old_card, target_card,
                    status, error, attempted_at, completed_at
                ) VALUES (
                    (SELECT id FROM card_operations WHERE event_key = ?),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    event_key,
                    event_key,
                    membership_id,
                    template,
                    old_card,
                    target_card,
                    status,
                    error,
                    attempted_at,
                    completed_at,
                ),
            )
            return cursor.rowcount > 0

    def mark_external_checked(
        self,
        *,
        application_id: int,
        actor_qq: str,
        observed_at: int | None = None,
    ) -> None:
        observed_at = observed_at or int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE applications
                SET external_checked_at = COALESCE(external_checked_at, ?),
                    external_actor_qq = CASE WHEN ? != '' THEN ? ELSE external_actor_qq END
                WHERE id = ?
                """,
                (observed_at, actor_qq, actor_qq, application_id),
            )

    def infer_external_rejections(
        self,
        *,
        platform_id: str,
        now: int,
        grace_seconds: int,
    ) -> int:
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT a.id, a.external_actor_qq, a.external_checked_at
                FROM applications a
                WHERE a.platform_id = ?
                  AND a.external_checked_at IS NOT NULL
                  AND a.external_checked_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM membership_sessions ms WHERE ms.application_id = a.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM application_actions aa
                      WHERE aa.application_id = a.id AND aa.kind = 'platform'
                        AND aa.action IN ('approve', 'reject')
                        AND aa.status IN ('succeeded', 'observed', 'inferred')
                  )
                """,
                (platform_id, now - grace_seconds),
            ).fetchall()
            count = 0
            for row in rows:
                inserted = self.record_action(
                    application_id=int(row["id"]),
                    kind="platform",
                    action="reject",
                    actor_qq=str(row["external_actor_qq"] or ""),
                    source="external_inferred",
                    status="inferred",
                    reason="外部管理员已处理且未观察到入群，按配置记为拒绝",
                    occurred_at=int(row["external_checked_at"]),
                )
                count += int(inserted)
            return count

    def get_application_id_by_flag(
        self,
        *,
        platform_id: str,
        self_id: str,
        flag: str,
    ) -> int | None:
        key = self.request_key(platform_id, self_id, flag)
        with self._lock:
            row = self._connection.execute(
                "SELECT id FROM applications WHERE request_key = ?",
                (key,),
            ).fetchone()
        return int(row["id"]) if row else None

    def has_review_action(self, application_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM application_actions
                WHERE application_id = ? AND kind = 'review' LIMIT 1
                """,
                (application_id,),
            ).fetchone()
        return row is not None

    def platform_ids(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT platform_id FROM applications WHERE platform_id != ''",
            ).fetchall()
        return [str(row["platform_id"]) for row in rows]

    def history(self, *, group_id: str, applicant_qq: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            applications = self._connection.execute(
                """
                SELECT * FROM applications
                WHERE group_id = ? AND applicant_qq = ?
                ORDER BY requested_at DESC, id DESC LIMIT ?
                """,
                (group_id, applicant_qq, limit),
            ).fetchall()
            orphan_memberships = self._connection.execute(
                """
                SELECT * FROM membership_sessions
                WHERE group_id = ? AND user_id = ? AND application_id IS NULL
                ORDER BY COALESCE(joined_at, left_at) DESC, id DESC LIMIT ?
                """,
                (group_id, applicant_qq, limit),
            ).fetchall()
            records = [self._application_detail(row) for row in applications]
            records.extend(self._join_only_detail(row) for row in orphan_memberships)
            records.sort(key=lambda item: int(item.get("requested_at") or 0), reverse=True)
            return records[:limit]

    def detail(self, *, group_id: str, application_id: int | str) -> dict[str, Any] | None:
        with self._lock:
            if isinstance(application_id, str) and application_id.upper().startswith("J"):
                try:
                    membership_id = int(application_id[1:])
                except ValueError:
                    return None
                membership_row = self._connection.execute(
                    """
                    SELECT * FROM membership_sessions
                    WHERE group_id = ? AND id = ? AND application_id IS NULL
                    """,
                    (group_id, membership_id),
                ).fetchone()
                return self._join_only_detail(membership_row) if membership_row else None
            row = self._connection.execute(
                "SELECT * FROM applications WHERE group_id = ? AND id = ?",
                (group_id, application_id),
            ).fetchone()
            return self._application_detail(row) if row else None

    def _application_detail(self, row: sqlite3.Row) -> dict[str, Any]:
        application = dict(row)
        application["actions"] = [
            dict(item)
            for item in self._connection.execute(
                """
                SELECT * FROM application_actions
                WHERE application_id = ? ORDER BY occurred_at, id
                """,
                (row["id"],),
            ).fetchall()
        ]
        memberships = []
        for membership_row in self._connection.execute(
            """
            SELECT * FROM membership_sessions
            WHERE application_id = ? ORDER BY joined_at, id
            """,
            (row["id"],),
        ).fetchall():
            memberships.append(self._membership_detail(membership_row))
        application["memberships"] = memberships
        application["record_type"] = "application"
        return application

    def _membership_detail(self, row: sqlite3.Row) -> dict[str, Any]:
        membership = dict(row)
        membership["card_operations"] = [
            dict(item)
            for item in self._connection.execute(
                """
                SELECT * FROM card_operations
                WHERE membership_id = ? ORDER BY attempted_at, id
                """,
                (row["id"],),
            ).fetchall()
        ]
        return membership

    def _join_only_detail(self, row: sqlite3.Row) -> dict[str, Any]:
        membership = self._membership_detail(row)
        return {
            "id": f"J{row['id']}",
            "record_type": "join_only",
            "group_id": row["group_id"],
            "applicant_qq": row["user_id"],
            "requested_at": row["joined_at"] or row["left_at"] or row["observed_at"],
            "nickname": row["nickname_at_join"],
            "question": "",
            "answer": "",
            "actions": [],
            "memberships": [membership],
        }
