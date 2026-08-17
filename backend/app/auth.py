from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .knowledge_bases import validate_knowledge_base_id

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
_USER_ID_PATTERN = re.compile(r"^usr_[a-f0-9]{16}$")
_SESSION_ID_PATTERN = re.compile(r"^ses_[a-f0-9]{16}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ROLES = {"admin", "member"}
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEY_LENGTH = 32


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    username: str
    display_name: str
    role: str
    active: bool
    password_hash: str
    created_at: datetime
    updated_at: datetime

    def to_public(self) -> dict[str, str | bool]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "active": self.active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_json(self) -> dict[str, str | bool]:
        return {**self.to_public(), "password_hash": self.password_hash}

    @classmethod
    def from_json(cls, data: dict[str, object]) -> UserRecord:
        user_id = str(data["user_id"])
        role = str(data["role"])
        if not _USER_ID_PATTERN.fullmatch(user_id) or role not in _ROLES:
            raise ValueError("user record is invalid")
        password_hash = str(data["password_hash"])
        _validate_password_hash(password_hash)
        return cls(
            user_id=user_id,
            username=validate_username(str(data["username"])),
            display_name=str(data["display_name"]),
            role=role,
            active=bool(data["active"]),
            password_hash=password_hash,
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
        )


@dataclass(frozen=True)
class AuthenticatedSession:
    user: UserRecord
    token: str
    expires_at: datetime


def validate_username(username: str) -> str:
    normalized = username.strip()
    if not _USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("username is invalid")
    return normalized


def validate_password(password: str) -> str:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("password length is invalid")
    return password


class AuthRepository:
    """原子保存账号、可撤销会话和知识库授权；原始会话令牌不落盘。"""

    def __init__(self, path: Path, session_ttl_hours: int = 12):
        self.path = path
        self.session_ttl = timedelta(hours=session_ttl_hours)
        self._lock = RLock()
        self._ensure_store()

    def has_users(self) -> bool:
        with self._lock:
            return bool(self._load()["users"])

    def bootstrap_admin(
        self,
        username: str,
        password: str,
        display_name: str,
    ) -> AuthenticatedSession:
        username = validate_username(username)
        password = validate_password(password)
        with self._lock:
            payload = self._load()
            if payload["users"]:
                raise ValueError("bootstrap already completed")
            user = self._new_user(username, password, display_name, "admin")
            payload["users"].append(user.to_json())
            session = self._append_session(payload, user)
            self._save(payload)
            return session

    def authenticate(self, username: str, password: str) -> AuthenticatedSession | None:
        try:
            normalized = validate_username(username)
        except ValueError:
            normalized = "invalid-user"
        with self._lock:
            payload = self._load()
            user = next(
                (
                    UserRecord.from_json(item)
                    for item in payload["users"]
                    if str(item["username"]).casefold() == normalized.casefold()
                ),
                None,
            )
            stored_hash = user.password_hash if user is not None else _dummy_password_hash()
            valid = _verify_password(password, stored_hash)
            if user is None or not user.active or not valid:
                return None
            session = self._append_session(payload, user)
            self._save(payload)
            return session

    def resolve_session(self, token: str) -> AuthenticatedSession | None:
        token_hash = _token_hash(token)
        now = datetime.now(UTC)
        with self._lock:
            payload = self._load()
            session_data = next(
                (
                    item
                    for item in payload["sessions"]
                    if hmac.compare_digest(str(item["token_hash"]), token_hash)
                ),
                None,
            )
            if session_data is None or session_data.get("revoked_at") is not None:
                return None
            expires_at = datetime.fromisoformat(str(session_data["expires_at"]))
            if expires_at <= now:
                return None
            user = self._find_user(payload, str(session_data["user_id"]))
            if user is None or not user.active:
                return None
            return AuthenticatedSession(user=user, token=token, expires_at=expires_at)

    def revoke_session(self, token: str) -> bool:
        token_hash = _token_hash(token)
        with self._lock:
            payload = self._load()
            session = next(
                (
                    item
                    for item in payload["sessions"]
                    if hmac.compare_digest(str(item["token_hash"]), token_hash)
                ),
                None,
            )
            if session is None or session.get("revoked_at") is not None:
                return False
            session["revoked_at"] = datetime.now(UTC).isoformat()
            self._save(payload)
            return True

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            users = [UserRecord.from_json(item) for item in self._load()["users"]]
        return sorted(users, key=lambda item: (item.role != "admin", item.username.casefold()))

    def get_user(self, user_id: str) -> UserRecord | None:
        _validate_user_id(user_id)
        with self._lock:
            return self._find_user(self._load(), user_id)

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str,
        role: str,
    ) -> UserRecord:
        username = validate_username(username)
        password = validate_password(password)
        _validate_role(role)
        with self._lock:
            payload = self._load()
            if any(str(item["username"]).casefold() == username.casefold() for item in payload["users"]):
                raise ValueError("username already exists")
            user = self._new_user(username, password, display_name, role)
            payload["users"].append(user.to_json())
            self._save(payload)
            return user

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
        with self._lock:
            payload = self._load()
            existing = self._find_user(payload, user_id)
            if existing is None:
                return None
            next_role = role if role is not None else existing.role
            next_active = active if active is not None else existing.active
            if existing.role == "admin" and existing.active and (next_role != "admin" or not next_active):
                active_admins = [
                    item
                    for item in payload["users"]
                    if item["role"] == "admin" and item["active"] is True
                ]
                if len(active_admins) == 1:
                    raise PermissionError("last active admin cannot be disabled")
            updated = UserRecord(
                user_id=existing.user_id,
                username=existing.username,
                display_name=display_name.strip() if display_name is not None else existing.display_name,
                role=next_role,
                active=next_active,
                password_hash=_hash_password(password) if password is not None else existing.password_hash,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )
            payload["users"] = [
                updated.to_json() if item["user_id"] == user_id else item for item in payload["users"]
            ]
            if not updated.active or password is not None:
                self._revoke_user_sessions(payload, user_id)
            self._save(payload)
            return updated

    def list_knowledge_base_users(self, knowledge_base_id: str) -> list[UserRecord]:
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
            user_ids = {
                str(item["user_id"])
                for item in payload["memberships"]
                if item["knowledge_base_id"] == knowledge_base_id
            }
            users = [
                UserRecord.from_json(item)
                for item in payload["users"]
                if item["user_id"] in user_ids and item["active"] is True
            ]
        return sorted(users, key=lambda item: item.username.casefold())

    def grant_knowledge_base(self, user_id: str, knowledge_base_id: str) -> bool:
        _validate_user_id(user_id)
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
            user = self._find_user(payload, user_id)
            if user is None or not user.active:
                raise LookupError("user not found")
            if any(
                item["user_id"] == user_id and item["knowledge_base_id"] == knowledge_base_id
                for item in payload["memberships"]
            ):
                return False
            payload["memberships"].append(
                {"user_id": user_id, "knowledge_base_id": knowledge_base_id}
            )
            self._save(payload)
            return True

    def revoke_knowledge_base(self, user_id: str, knowledge_base_id: str) -> bool:
        _validate_user_id(user_id)
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
            memberships = [
                item
                for item in payload["memberships"]
                if not (
                    item["user_id"] == user_id
                    and item["knowledge_base_id"] == knowledge_base_id
                )
            ]
            if len(memberships) == len(payload["memberships"]):
                return False
            payload["memberships"] = memberships
            self._save(payload)
            return True

    def remove_knowledge_base(self, knowledge_base_id: str) -> None:
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
            payload["memberships"] = [
                item
                for item in payload["memberships"]
                if item["knowledge_base_id"] != knowledge_base_id
            ]
            self._save(payload)

    def can_access_knowledge_base(self, user: UserRecord, knowledge_base_id: str) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        if not user.active:
            return False
        if user.role == "admin":
            return True
        with self._lock:
            payload = self._load()
            return any(
                item["user_id"] == user.user_id
                and item["knowledge_base_id"] == knowledge_base_id
                for item in payload["memberships"]
            )

    def accessible_knowledge_base_ids(self, user: UserRecord) -> set[str] | None:
        if user.role == "admin":
            return None
        with self._lock:
            payload = self._load()
            return {
                str(item["knowledge_base_id"])
                for item in payload["memberships"]
                if item["user_id"] == user.user_id
            }

    def _ensure_store(self) -> None:
        with self._lock:
            if self.path.exists():
                self._load()
                return
            self._save({"version": 1, "users": [], "sessions": [], "memberships": []})

    def _load(self) -> dict[str, list[dict[str, object]] | int]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            payload.get("version") != 1
            or not isinstance(payload.get("users"), list)
            or not isinstance(payload.get("sessions"), list)
            or not isinstance(payload.get("memberships"), list)
        ):
            raise ValueError("auth store format is invalid")
        users = [UserRecord.from_json(item) for item in payload["users"]]
        user_ids = {item.user_id for item in users}
        if len(user_ids) != len(users) or len({item.username.casefold() for item in users}) != len(users):
            raise ValueError("auth store contains duplicate users")
        for item in payload["sessions"]:
            if (
                not _SESSION_ID_PATTERN.fullmatch(str(item.get("session_id", "")))
                or str(item.get("user_id", "")) not in user_ids
                or not _SHA256_PATTERN.fullmatch(str(item.get("token_hash", "")))
            ):
                raise ValueError("auth session record is invalid")
            datetime.fromisoformat(str(item["created_at"]))
            datetime.fromisoformat(str(item["expires_at"]))
            if item.get("revoked_at") is not None:
                datetime.fromisoformat(str(item["revoked_at"]))
        membership_keys: set[tuple[str, str]] = set()
        for item in payload["memberships"]:
            user_id = str(item.get("user_id", ""))
            knowledge_base_id = validate_knowledge_base_id(str(item.get("knowledge_base_id", "")))
            if user_id not in user_ids:
                raise ValueError("auth membership user is invalid")
            key = (user_id, knowledge_base_id)
            if key in membership_keys:
                raise ValueError("auth store contains duplicate memberships")
            membership_keys.add(key)
        return payload

    def _save(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    def _append_session(
        self,
        payload: dict[str, object],
        user: UserRecord,
    ) -> AuthenticatedSession:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + self.session_ttl
        sessions = payload["sessions"]
        assert isinstance(sessions, list)
        sessions[:] = [
            item
            for item in sessions
            if item.get("revoked_at") is None
            and datetime.fromisoformat(str(item["expires_at"])) > now
        ]
        sessions.append(
            {
                "session_id": f"ses_{uuid4().hex[:16]}",
                "user_id": user.user_id,
                "token_hash": _token_hash(token),
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "revoked_at": None,
            }
        )
        return AuthenticatedSession(user=user, token=token, expires_at=expires_at)

    @staticmethod
    def _find_user(payload: dict[str, object], user_id: str) -> UserRecord | None:
        users = payload["users"]
        assert isinstance(users, list)
        item = next((item for item in users if item["user_id"] == user_id), None)
        return UserRecord.from_json(item) if item is not None else None

    @staticmethod
    def _new_user(username: str, password: str, display_name: str, role: str) -> UserRecord:
        _validate_role(role)
        now = datetime.now(UTC)
        return UserRecord(
            user_id=f"usr_{uuid4().hex[:16]}",
            username=username,
            display_name=display_name.strip() or username,
            role=role,
            active=True,
            password_hash=_hash_password(password),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _revoke_user_sessions(payload: dict[str, object], user_id: str) -> None:
        sessions = payload["sessions"]
        assert isinstance(sessions, list)
        now = datetime.now(UTC).isoformat()
        for session in sessions:
            if session["user_id"] == user_id and session.get("revoked_at") is None:
                session["revoked_at"] = now


def _validate_user_id(user_id: str) -> str:
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise ValueError("user id is invalid")
    return user_id


def _validate_role(role: str) -> str:
    if role not in _ROLES:
        raise ValueError("role is invalid")
    return role


def _hash_password(password: str, salt: bytes | None = None) -> str:
    validate_password(password)
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_KEY_LENGTH,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${actual_salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        _validate_password_hash(encoded)
        algorithm, n, r, p, salt, expected = encoded.split("$", maxsplit=5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected)),
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, bytes.fromhex(expected))


@lru_cache
def _dummy_password_hash() -> str:
    return _hash_password("not-a-real-password", salt=b"\0" * 16)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_password_hash(encoded: str) -> None:
    try:
        algorithm, n, r, p, salt, digest = encoded.split("$", maxsplit=5)
        valid = (
            algorithm == "scrypt"
            and int(n) == _SCRYPT_N
            and int(r) == _SCRYPT_R
            and int(p) == _SCRYPT_P
            and len(bytes.fromhex(salt)) == 16
            and len(bytes.fromhex(digest)) == _SCRYPT_KEY_LENGTH
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("password hash format is invalid")
