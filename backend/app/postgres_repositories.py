from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from .auth import (
    AuthenticatedSession,
    UserRecord,
    _hash_password,
    _token_hash,
    _validate_role,
    _validate_user_id,
    _verify_password,
    validate_password,
    validate_username,
)
from .knowledge_bases import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    KnowledgeBaseRecord,
    validate_knowledge_base_id,
)


def _user(row: dict[str, object]) -> UserRecord:
    return UserRecord(
        user_id=str(row["user_id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"]),
        role=str(row["role"]),
        active=bool(row["active"]),
        password_hash=str(row["password_hash"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresKnowledgeBaseRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """INSERT INTO knowledge_bases
                   (knowledge_base_id, name, name_normalized, description, is_default,
                    created_at, updated_at)
                   SELECT 'kb_default', '默认知识库', '默认知识库', '默认知识库。', true,
                          now(), now()
                   WHERE NOT EXISTS (SELECT 1 FROM knowledge_bases)"""
            )

    def list(self) -> list[KnowledgeBaseRecord]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_bases
                   ORDER BY is_default DESC, created_at, lower(name)"""
            ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, knowledge_base_id: str) -> KnowledgeBaseRecord | None:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_bases WHERE knowledge_base_id = %s",
                (knowledge_base_id,),
            ).fetchone()
        return self._record(row) if row else None

    def create(self, name: str, description: str) -> KnowledgeBaseRecord:
        now = datetime.now(UTC)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """INSERT INTO knowledge_bases
                       (knowledge_base_id, name, name_normalized, description, is_default,
                        created_at, updated_at)
                       VALUES (%s, %s, %s, %s, false, %s, %s) RETURNING *""",
                    (f"kb_{uuid4().hex[:12]}", name, name.casefold(), description, now, now),
                ).fetchone()
        except errors.UniqueViolation as exc:
            raise ValueError("knowledge base name already exists") from exc
        return self._record(row)

    def update(self, knowledge_base_id: str, name: str, description: str) -> KnowledgeBaseRecord | None:
        validate_knowledge_base_id(knowledge_base_id)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """UPDATE knowledge_bases SET name = %s, name_normalized = %s,
                              description = %s, updated_at = now()
                       WHERE knowledge_base_id = %s RETURNING *""",
                    (name, name.casefold(), description, knowledge_base_id),
                ).fetchone()
        except errors.UniqueViolation as exc:
            raise ValueError("knowledge base name already exists") from exc
        return self._record(row) if row else None

    def delete(self, knowledge_base_id: str) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        if knowledge_base_id == DEFAULT_KNOWLEDGE_BASE_ID:
            raise ValueError("default knowledge base cannot be deleted")
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute(
                "DELETE FROM knowledge_bases WHERE knowledge_base_id = %s", (knowledge_base_id,)
            )
        return result.rowcount > 0

    @staticmethod
    def _record(row: dict[str, object]) -> KnowledgeBaseRecord:
        return KnowledgeBaseRecord(
            knowledge_base_id=str(row["knowledge_base_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_default=bool(row["is_default"]),
        )


class PostgresDataSourceRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def list(self, accessible_ids: set[str] | None = None) -> list[dict[str, object]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT s.data_source_id, s.name, s.source_type, s.knowledge_base_id,
                          k.name AS knowledge_base_name, s.enabled, s.updated_at,
                          count(DISTINCT d.document_id) AS document_count,
                          COALESCE(sum(v.source_file_bytes), 0) AS source_file_bytes,
                          j.finished_at AS last_synced_at, j.status AS sync_status,
                          j.failure_reason
                   FROM data_sources s JOIN knowledge_bases k USING (knowledge_base_id)
                   LEFT JOIN documents d ON d.data_source_id = s.data_source_id
                   LEFT JOIN document_versions v ON v.document_version_id = d.current_version_id
                   LEFT JOIN LATERAL (
                     SELECT status, finished_at, failure_reason FROM index_jobs
                     WHERE data_source_id = s.data_source_id
                     ORDER BY created_at DESC LIMIT 1
                   ) j ON true
                   GROUP BY s.data_source_id, k.name, j.finished_at, j.status, j.failure_reason
                   ORDER BY s.updated_at DESC"""
            ).fetchall()
        if accessible_ids is not None:
            rows = [row for row in rows if row["knowledge_base_id"] in accessible_ids]
        return [dict(row) for row in rows]

    def list_document_versions(self, knowledge_base_id: str) -> list[dict[str, object]]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT v.document_version_id, v.document_id, d.filename,
                          v.version_number, v.content_sha256, v.source_file_bytes,
                          s.source_type, v.status, v.failure_reason, v.created_at,
                          v.indexed_at,
                          COALESCE(d.current_version_id = v.document_version_id, false) AS is_current
                   FROM document_versions v
                   JOIN documents d USING (knowledge_base_id, document_id)
                   JOIN data_sources s USING (data_source_id)
                   WHERE v.knowledge_base_id = %s
                   ORDER BY d.filename, v.version_number DESC""",
                (knowledge_base_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_enabled(self, data_source_id: str, enabled: bool) -> bool:
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute(
                "UPDATE data_sources SET enabled = %s, updated_at = now() WHERE data_source_id = %s",
                (enabled, data_source_id),
            )
        return result.rowcount > 0

    def sync_payload(self, data_source_id: str) -> dict[str, object] | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT s.knowledge_base_id, s.name, s.enabled, v.source_path
                   FROM data_sources s
                   LEFT JOIN documents d ON d.data_source_id = s.data_source_id
                   LEFT JOIN document_versions v ON v.document_version_id = d.current_version_id
                   WHERE s.data_source_id = %s""",
                (data_source_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete(self, data_source_id: str) -> bool:
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            row = connection.execute(
                "SELECT count(*) FROM documents WHERE data_source_id = %s", (data_source_id,)
            ).fetchone()
            if row is None:
                return False
            if row[0]:
                raise ValueError("data source has documents")
            active = connection.execute(
                """SELECT EXISTS (SELECT 1 FROM index_jobs
                   WHERE data_source_id = %s AND status IN ('queued','running'))""",
                (data_source_id,),
            ).fetchone()[0]
            if active:
                raise ValueError("data source has active jobs")
            result = connection.execute(
                "DELETE FROM data_sources WHERE data_source_id = %s", (data_source_id,)
            )
        return result.rowcount > 0


class PostgresAuthRepository:
    def __init__(self, database_url: str, session_ttl_hours: int = 12):
        self.database_url = database_url
        self.session_ttl = timedelta(hours=session_ttl_hours)

    def has_users(self) -> bool:
        with psycopg.connect(self.database_url) as connection:
            return bool(connection.execute("SELECT EXISTS (SELECT 1 FROM users)").fetchone()[0])

    def bootstrap_admin(self, username: str, password: str, display_name: str) -> AuthenticatedSession:
        username = validate_username(username)
        validate_password(password)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                if connection.execute("SELECT 1 FROM users LIMIT 1 FOR UPDATE").fetchone():
                    raise ValueError("bootstrap already completed")
                user = self._insert_user(connection, username, password, display_name, "admin")
                return self._append_session(connection, user)

    def authenticate(self, username: str, password: str) -> AuthenticatedSession | None:
        try:
            normalized = validate_username(username).casefold()
        except ValueError:
            normalized = "invalid-user"
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                row = connection.execute(
                    "SELECT * FROM users WHERE username_normalized = %s", (normalized,)
                ).fetchone()
                if (
                    row is None
                    or not row["active"]
                    or not _verify_password(password, str(row["password_hash"]))
                ):
                    return None
                return self._append_session(connection, _user(row))

    def resolve_session(self, token: str) -> AuthenticatedSession | None:
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT s.expires_at, u.* FROM sessions s
                   JOIN users u ON u.user_id = s.user_id
                   WHERE s.token_hash = %s AND s.revoked_at IS NULL
                     AND s.expires_at > %s AND u.active""",
                (_token_hash(token), now),
            ).fetchone()
        return AuthenticatedSession(_user(row), token, row["expires_at"]) if row else None

    def revoke_session(self, token: str) -> bool:
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute(
                """UPDATE sessions SET revoked_at = now()
                   WHERE token_hash = %s AND revoked_at IS NULL""",
                (_token_hash(token),),
            )
        return result.rowcount > 0

    def list_users(self) -> list[UserRecord]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY (role <> 'admin'), lower(username)"
            ).fetchall()
        return [_user(row) for row in rows]

    def get_user(self, user_id: str) -> UserRecord | None:
        _validate_user_id(user_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = %s", (user_id,)).fetchone()
        return _user(row) if row else None

    def create_user(self, username: str, password: str, display_name: str, role: str) -> UserRecord:
        username = validate_username(username)
        validate_password(password)
        _validate_role(role)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                return self._insert_user(connection, username, password, display_name, role)
        except errors.UniqueViolation as exc:
            raise ValueError("username already exists") from exc

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None,
        role: str | None,
        active: bool | None,
        password: str | None,
    ) -> UserRecord | None:
        _validate_user_id(user_id)
        if role is not None:
            _validate_role(role)
        if password is not None:
            validate_password(password)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                row = connection.execute(
                    "SELECT * FROM users WHERE user_id = %s FOR UPDATE", (user_id,)
                ).fetchone()
                if row is None:
                    return None
                next_role = role or str(row["role"])
                next_active = bool(row["active"]) if active is None else active
                if row["role"] == "admin" and row["active"] and (next_role != "admin" or not next_active):
                    count = connection.execute(
                        "SELECT count(*) FROM users WHERE role = 'admin' AND active"
                    ).fetchone()[0]
                    if count == 1:
                        raise PermissionError("last active admin cannot be disabled")
                updated = connection.execute(
                    """UPDATE users SET display_name = %s, role = %s, active = %s,
                              password_hash = %s, updated_at = now()
                       WHERE user_id = %s RETURNING *""",
                    (
                        display_name.strip() if display_name is not None else row["display_name"],
                        next_role,
                        next_active,
                        _hash_password(password) if password is not None else row["password_hash"],
                        user_id,
                    ),
                ).fetchone()
                if not next_active or password is not None:
                    connection.execute(
                        "UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
                        (user_id,),
                    )
        return _user(updated)

    def list_knowledge_base_users(self, knowledge_base_id: str) -> list[UserRecord]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT u.* FROM users u JOIN knowledge_base_memberships m USING (user_id)
                   WHERE m.knowledge_base_id = %s ORDER BY lower(u.username)""",
                (knowledge_base_id,),
            ).fetchall()
        return [_user(row) for row in rows]

    def grant_knowledge_base(self, user_id: str, knowledge_base_id: str) -> bool:
        _validate_user_id(user_id)
        validate_knowledge_base_id(knowledge_base_id)
        try:
            with psycopg.connect(self.database_url) as connection:
                result = connection.execute(
                    """INSERT INTO knowledge_base_memberships(user_id, knowledge_base_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (user_id, knowledge_base_id),
                )
        except errors.ForeignKeyViolation as exc:
            raise LookupError("user or knowledge base not found") from exc
        return result.rowcount > 0

    def revoke_knowledge_base(self, user_id: str, knowledge_base_id: str) -> bool:
        _validate_user_id(user_id)
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute(
                """DELETE FROM knowledge_base_memberships
                   WHERE user_id = %s AND knowledge_base_id = %s""",
                (user_id, knowledge_base_id),
            )
        return result.rowcount > 0

    def remove_knowledge_base(self, knowledge_base_id: str) -> None:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "DELETE FROM knowledge_base_memberships WHERE knowledge_base_id = %s",
                (knowledge_base_id,),
            )

    def can_access_knowledge_base(self, user: UserRecord, knowledge_base_id: str) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        if user.role == "admin":
            return True
        with psycopg.connect(self.database_url) as connection:
            return bool(
                connection.execute(
                    """SELECT EXISTS (SELECT 1 FROM knowledge_base_memberships
                       WHERE user_id = %s AND knowledge_base_id = %s)""",
                    (user.user_id, knowledge_base_id),
                ).fetchone()[0]
            )

    def accessible_knowledge_base_ids(self, user: UserRecord) -> set[str] | None:
        if user.role == "admin":
            return None
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT knowledge_base_id FROM knowledge_base_memberships WHERE user_id = %s",
                (user.user_id,),
            ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _insert_user(
        connection: psycopg.Connection[dict[str, object]],
        username: str,
        password: str,
        display_name: str,
        role: str,
    ) -> UserRecord:
        now = datetime.now(UTC)
        row = connection.execute(
            """INSERT INTO users
               (user_id, username, username_normalized, display_name, role, active,
                password_hash, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, true, %s, %s, %s) RETURNING *""",
            (
                f"usr_{uuid4().hex[:16]}",
                username,
                username.casefold(),
                display_name.strip() or username,
                role,
                _hash_password(password),
                now,
                now,
            ),
        ).fetchone()
        return _user(row)

    def _append_session(
        self, connection: psycopg.Connection[dict[str, object]], user: UserRecord
    ) -> AuthenticatedSession:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + self.session_ttl
        connection.execute("DELETE FROM sessions WHERE expires_at <= %s", (now,))
        connection.execute(
            """INSERT INTO sessions
               (session_id, user_id, token_hash, created_at, expires_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (f"ses_{uuid4().hex[:16]}", user.user_id, _token_hash(token), now, expires_at),
        )
        return AuthenticatedSession(user, token, expires_at)
