# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
"""
Security audit trail: who did what, to what, when, from where.

Every event is written to two places:
  * the append-only table `lauretta_audit_log` in the metadata database, and
  * one JSON line on stdout (logger `lauretta.audit`), for a log shipper / SIEM.

Captured events (see lauretta/docs/12-audit-trail.md for the full list):
  * auth.login (success and failure, with the reason) and auth.logout
  * user.create / user.update / user.delete
  * role.assign / role.revoke (user <-> role), role.permission_add / _remove,
    role.create / role.update / role.delete, and the group equivalents
  * data.create / data.update / data.delete for every other ORM-mapped table in the
    metadata database (dashboards, charts, datasets, databases, ...), minus the
    bookkeeping tables in EXCLUDED_TABLES
  * data.sql_dml: a SQL Lab statement that writes to a data source
  * data.file_upload: a floor-map image upload

Changes made through the ORM are captured in `after_flush` and inserted on the same
connection, so the audit row commits or rolls back with the change it describes; the
stdout line is printed only after the commit. Logins and logouts are written in their
own short transaction.

Tamper protection: database triggers reject UPDATE, DELETE and TRUNCATE on the table,
and each row carries an HMAC over its contents (`verify` recomputes them). The HMAC key
is LAURETTA_AUDIT_HMAC_KEY, or one derived from SECRET_KEY when that is unset. The
HMAC shows that a row was altered; a deleted row shows up only as a gap in the ids,
which the stdout copy settles.

Command line (PYTHONPATH must include this directory, as in every compose mode):
  python -m lauretta_audit create-table   # init scripts; idempotent
  python -m lauretta_audit verify         # recompute every row's HMAC
"""

from __future__ import annotations

import enum
import hashlib
import hmac

# The standard library on purpose: the row HMAC is computed over this exact
# serialisation, so it must not change with superset.utils.json.
import json  # noqa: TID251
import logging
import os
import socket
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction

if TYPE_CHECKING:
    from flask import Flask
    from sqlalchemy.orm.relationships import RelationshipProperty

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("lauretta.audit")

TABLE_NAME = "lauretta_audit_log"
TRIGGER_FUNCTION = "lauretta_audit_log_append_only"

# Set by the init scripts (docker/docker-init.sh, docker/docker-init-ha.sh): the CLI
# commands they run before `create-table` must not fail on the missing table.
INIT_ENV = "LAURETTA_AUDIT_INIT"
KEY_ENV = "LAURETTA_AUDIT_HMAC_KEY"
# `verify` only: comma-separated earlier LAURETTA_AUDIT_HMAC_KEY / SUPERSET_SECRET_KEY.
OLD_KEYS_ENV = "LAURETTA_AUDIT_OLD_KEYS"

# Longest stored value (characters) before it is cut and replaced by a hash.
MAX_VALUE_CHARS = 1000

# Metadata tables whose rows are not business data: request bookkeeping, caches,
# SQL Lab editor state, and the permission catalogue `superset init` maintains.
EXCLUDED_TABLES = frozenset(
    {
        TABLE_NAME,
        "logs",  # Superset's own event log
        "query",  # SQL Lab history; mutating statements are data.sql_dml instead
        "key_value",  # permalinks, filter state, explore form data
        "keyvalue",
        "cache_keys",
        "tab_state",  # SQL Lab editor tabs
        "table_schema",
        "favstar",
        "tasks",
        "report_execution_log",
        "ha_sessions",  # server-side sessions (HA mode)
        "ab_permission",
        "ab_view_menu",
        "ab_permission_view",
        "database_user_oauth2_tokens",
    }
)

# Columns that are never stored, only flagged as changed.
SENSITIVE_COLUMNS = frozenset(
    {
        "password",
        "encrypted_extra",
        "server_cert",
        "private_key",
        "private_key_password",
        "access_token",
        "refresh_token",
        "api_key",
    }
)
SENSITIVE_MARKERS = ("password", "secret", "token")
REDACTED = "[REDACTED]"

# Bookkeeping columns left out of update diffs and snapshots.
NOISE_COLUMNS = frozenset(
    {"changed_on", "changed_by_fk", "created_on", "created_by_fk"}
)
# FAB updates these on every login; they are covered by auth.login.
USER_NOISE_COLUMNS = frozenset({"last_login", "login_count", "fail_login_count"})

SECURITY_CATEGORIES = {"ab_user": "user", "ab_role": "role", "ab_group": "group"}

# Fields covered by the row HMAC, in a fixed order.
HASHED_FIELDS = (
    "event_time",
    "category",
    "action",
    "outcome",
    "actor_id",
    "actor_username",
    "target_type",
    "target_id",
    "target_name",
    "details",
    "ip_address",
    "user_agent",
    "request",
    "node",
    "key_id",
)

_metadata = sa.MetaData()
audit_table = sa.Table(
    TABLE_NAME,
    _metadata,
    sa.Column(
        "id",
        sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("category", sa.String(32), nullable=False),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("outcome", sa.String(16), nullable=False),
    sa.Column("actor_id", sa.Integer()),
    sa.Column("actor_username", sa.String(256)),
    sa.Column("target_type", sa.String(128)),
    sa.Column("target_id", sa.String(128)),
    sa.Column("target_name", sa.String(512)),
    sa.Column("details", sa.Text()),
    sa.Column("ip_address", sa.String(64)),
    sa.Column("user_agent", sa.String(512)),
    sa.Column("request", sa.String(512)),
    sa.Column("node", sa.String(128)),
    sa.Column("key_id", sa.String(16), nullable=False),
    sa.Column("row_hmac", sa.String(64), nullable=False),
    sa.Index("ix_lauretta_audit_log_event_time", "event_time"),
    sa.Index("ix_lauretta_audit_log_actor", "actor_username"),
    sa.Index("ix_lauretta_audit_log_action", "category", "action"),
)


@dataclass
class _State:
    initialised: bool = False
    db_enabled: bool = False
    key: bytes = b""
    key_id: str = ""
    node: str = field(default_factory=socket.gethostname)


_state = _State()

# session.info key: [(savepoint or None, [records])] written but not yet committed.
_PENDING = "lauretta_audit_pending"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def security_manager_class() -> type:
    """CUSTOM_SECURITY_MANAGER: Superset's manager plus login auditing."""
    from superset.security import SupersetSecurityManager

    class LaurettaSecurityManager(SupersetSecurityManager):
        def auth_user_db(self, username: str, password: str) -> Any:
            user = super().auth_user_db(username, password)
            _audit_login(self, "db", username, user)
            return user

        def auth_user_ldap(self, username: str, password: str) -> Any:
            user = super().auth_user_ldap(username, password)
            _audit_login(self, "ldap", username, user)
            return user

        def auth_user_oauth(self, userinfo: dict[str, Any]) -> Any:
            user = super().auth_user_oauth(userinfo)
            name = userinfo.get("username") or userinfo.get("email") or ""
            _audit_login(self, "oauth", str(name), user)
            return user

        def auth_user_remote_user(self, username: str) -> Any:
            user = super().auth_user_remote_user(username)
            _audit_login(self, "remote_user", username, user)
            return user

    return LaurettaSecurityManager


def init_app(app: Flask) -> None:
    """FLASK_APP_MUTATOR hook: stdout sink, HMAC key, ORM and logout listeners.

    Outside the init scripts a missing table is a hard error: the web must not run
    without its audit trail. Inside them (INIT_ENV=1) events go to stdout only until
    `create-table` has run.
    """
    _configure_stdout()
    _state.key, _state.key_id = _hmac_key(app)

    from superset.extensions import db

    with app.app_context():
        has_table = sa_inspect(db.engine).has_table(TABLE_NAME)
    if not has_table:
        if os.environ.get(INIT_ENV) != "1":
            raise RuntimeError(
                f"audit table {TABLE_NAME!r} is missing from the metadata database: "
                "run the init (docker/docker-init.sh, or docker/docker-init-ha.sh in "
                "HA mode), which creates it"
            )
        logger.warning("audit table %s missing: events go to stdout only", TABLE_NAME)
    _state.db_enabled = has_table

    if not _state.initialised:
        from flask_login import user_logged_out

        event.listen(Session, "after_flush", _after_flush)
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)
        user_logged_out.connect(_on_logout)
        _state.initialised = True
    logger.info("audit trail active (table: %s)", "yes" if has_table else "no")


def _configure_stdout() -> None:
    if any(getattr(h, "_lauretta_audit", False) for h in audit_logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._lauretta_audit = True  # type: ignore[attr-defined]
    audit_logger.addHandler(handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False


def _derived_key(secret: str) -> bytes:
    return hmac.new(secret.encode(), b"lauretta-audit-log-v1", hashlib.sha256).digest()


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:12]


def _hmac_key(app: Flask) -> tuple[bytes, str]:
    raw = os.environ.get(KEY_ENV, "").strip()
    key = raw.encode() if raw else _derived_key(str(app.config["SECRET_KEY"]))
    return key, _key_id(key)


def create_table() -> None:
    """Create the table and its append-only triggers. Idempotent; init scripts only.

    An advisory lock serialises concurrent runs on PostgreSQL. An existing table is
    left alone apart from re-adding missing triggers, so a table that a DBA has
    handed to a separate owner role (lauretta/docs/12-audit-trail.md) is not touched.
    """
    from superset.extensions import db

    with db.engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "postgresql":
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:n))"), {"n": TABLE_NAME}
            )
        if not sa_inspect(conn).has_table(TABLE_NAME):
            audit_table.create(conn)
            print(f"audit: created table {TABLE_NAME}")
        if dialect == "postgresql":
            _pg_triggers(conn)
        elif dialect == "sqlite":
            _sqlite_triggers(conn)
        else:
            print(f"audit: no append-only triggers for dialect {dialect}")


def _pg_triggers(conn: Connection) -> None:
    present = {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = CAST(:t AS regclass) AND NOT tgisinternal"
            ),
            {"t": TABLE_NAME},
        )
    }
    wanted = {
        f"{TABLE_NAME}_no_update_delete": "BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW",
        f"{TABLE_NAME}_no_truncate": "BEFORE TRUNCATE ON {t} FOR EACH STATEMENT",
    }
    if set(wanted) <= present:
        return
    conn.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION {TRIGGER_FUNCTION}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '{TABLE_NAME} is append-only: % is not allowed', TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END
            $$
            """
        )
    )
    for name, when in wanted.items():
        if name not in present:
            conn.execute(
                sa.text(
                    f"CREATE TRIGGER {name} {when.format(t=TABLE_NAME)} "
                    f"EXECUTE FUNCTION {TRIGGER_FUNCTION}()"
                )
            )
            print(f"audit: created trigger {name}")
    conn.execute(
        sa.text(f"REVOKE UPDATE, DELETE, TRUNCATE ON {TABLE_NAME} FROM PUBLIC")
    )


def _sqlite_triggers(conn: Connection) -> None:
    for op in ("UPDATE", "DELETE"):
        conn.execute(
            sa.text(
                f"CREATE TRIGGER IF NOT EXISTS {TABLE_NAME}_no_{op.lower()} "
                f"BEFORE {op} ON {TABLE_NAME} BEGIN "
                f"SELECT RAISE(ABORT, '{TABLE_NAME} is append-only'); END"
            )
        )


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _sign(record: dict[str, Any], key: bytes) -> str:
    payload = {name: record.get(name) for name in HASHED_FIELDS}
    payload["event_time"] = _iso(record["event_time"])
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def _cut(value: Optional[str], limit: int) -> Optional[str]:
    return value if value is None or len(value) <= limit else value[:limit]


def _context() -> dict[str, Any]:
    """Actor and request of the current event."""
    from flask import g, has_request_context, request

    ctx: dict[str, Any] = {
        "actor_id": None,
        "actor_username": None,
        "ip_address": None,
        "user_agent": None,
        "request": None,
    }
    if not has_request_context():
        ctx["actor_username"] = "system"
        ctx["request"] = _cut(
            "cli: " + " ".join(os.path.basename(a) for a in sys.argv[:3]), 512
        )
        return ctx
    user = getattr(g, "user", None)
    if user is None:
        from flask_login import current_user

        user = current_user
    try:
        if user is not None and getattr(user, "is_authenticated", False):
            ctx["actor_id"] = getattr(user, "id", None)
            ctx["actor_username"] = getattr(user, "username", None)
        else:
            ctx["actor_username"] = "anonymous"
    except Exception:  # noqa: BLE001 -- a detached/expired user must not block the write
        ctx["actor_username"] = "unknown"
    ctx["ip_address"] = _cut(request.remote_addr, 64)
    ctx["user_agent"] = _cut(request.user_agent.string or None, 512)
    ctx["request"] = _cut(f"{request.method} {request.path}", 512)
    return ctx


def _record(
    category: str,
    action: str,
    *,
    outcome: str = "success",
    target_type: Optional[str] = None,
    target_id: Any = None,
    target_name: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "event_time": datetime.now(timezone.utc),
        "category": category,
        "action": action,
        "outcome": outcome,
        "target_type": target_type,
        "target_id": None if target_id is None else _cut(str(target_id), 128),
        "target_name": _cut(None if target_name is None else str(target_name), 512),
        "details": json.dumps(
            details or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
        "node": _cut(_state.node, 128),
        "key_id": _state.key_id,
    }
    record.update(_context())
    # Who and where, when the request context does not tell (logins, SQL Lab threads).
    if overrides:
        record.update({k: v for k, v in overrides.items() if v is not None})
    record["actor_username"] = _cut(record.get("actor_username"), 256)
    record["row_hmac"] = _sign(record, _state.key)
    return record


def _emit_stdout(records: list[dict[str, Any]]) -> None:
    for record in records:
        line = dict(record)
        line["event_time"] = _iso(record["event_time"])
        line["details"] = json.loads(record["details"])
        audit_logger.info(
            json.dumps({"lauretta_audit": 1, **line}, ensure_ascii=False, default=str)
        )


def _insert(conn: Connection, records: list[dict[str, Any]]) -> None:
    for record in records:
        result = conn.execute(audit_table.insert().values(**record))
        record["id"] = result.inserted_primary_key[0]


def record_event(category: str, action: str, **kwargs: Any) -> None:
    """Write one event in its own transaction (logins, logouts, file uploads).

    A failed database write is logged and does not fail the caller; the stdout line
    is still printed.
    """
    if not _state.initialised:
        return
    record = _record(category, action, **kwargs)
    if _state.db_enabled:
        try:
            from superset.extensions import db

            with db.engine.begin() as conn:
                _insert(conn, [record])
        except Exception:  # noqa: BLE001
            logger.exception("audit: could not write %s to %s", action, TABLE_NAME)
    _emit_stdout([record])


# ---------------------------------------------------------------------------
# Logins and logouts
# ---------------------------------------------------------------------------
def _audit_login(sm: Any, method: str, username: str, user: Any) -> None:
    try:
        if user:
            record_event(
                "auth",
                "auth.login",
                target_type="ab_user",
                target_id=user.id,
                target_name=user.username,
                details={"method": method},
                overrides={"actor_id": user.id, "actor_username": user.username},
            )
            return
        existing = sm.find_user(username=username) if username else None
        if existing is None:
            reason = "unknown_user"
        elif not existing.is_active:
            reason = "inactive_user"
        else:
            reason = "invalid_credentials"
        record_event(
            "auth",
            "auth.login",
            outcome="failure",
            target_type="ab_user",
            target_id=getattr(existing, "id", None),
            target_name=username,
            details={"method": method, "reason": reason},
            overrides={
                "actor_id": getattr(existing, "id", None),
                "actor_username": username or "",
            },
        )
    except Exception:  # noqa: BLE001 -- auditing must never break a login
        logger.exception("audit: could not record a login")


def _on_logout(sender: Any, user: Any = None, **_: Any) -> None:
    try:
        if user is None or not getattr(user, "is_authenticated", False):
            return
        record_event(
            "auth",
            "auth.logout",
            target_type="ab_user",
            target_id=user.id,
            target_name=user.username,
            overrides={"actor_id": user.id, "actor_username": user.username},
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit: could not record a logout")


# ---------------------------------------------------------------------------
# ORM changes
# ---------------------------------------------------------------------------
def _table_name(obj: Any) -> str:
    table = sa_inspect(obj).mapper.local_table
    return str(getattr(table, "name", type(obj).__name__))


def _pk(obj: Any) -> Any:
    # Not insp.identity: new rows only get their identity key after the flush.
    insp = sa_inspect(obj)
    mapper = insp.mapper
    values = [
        insp.dict.get(mapper.get_property_by_column(col).key)
        for col in mapper.primary_key
    ]
    if all(v is None for v in values):
        return None
    return values[0] if len(values) == 1 else values


_LABEL_ATTRS = (
    "username",
    "dashboard_title",
    "slice_name",
    "table_name",
    "database_name",
    "column_name",
    "metric_name",
    "label",
    "name",
    "short_descr",
)


def _label(obj: Any) -> Optional[str]:
    loaded = sa_inspect(obj).dict
    for attr in _LABEL_ATTRS:
        value = loaded.get(attr)
        if value:
            return str(value)
    if loaded.get("uuid"):
        return str(loaded["uuid"])
    return None


def _ref(obj: Any) -> str:
    """Short reference to a related row: 'label (#id)'."""
    if _table_name(obj) == "ab_permission_view":
        try:
            return f"{obj.permission.name} on {obj.view_menu.name}"
        except Exception:  # noqa: BLE001
            logger.debug("audit: permission name unavailable", exc_info=True)
    label = _label(obj)
    pk = _pk(obj)
    return f"{label} (#{pk})" if label else f"#{pk}"


def _is_sensitive(name: str, column: Any) -> bool:
    from sqlalchemy_utils import EncryptedType

    if name in SENSITIVE_COLUMNS or any(m in name for m in SENSITIVE_MARKERS):
        return True
    return isinstance(getattr(column, "type", None), EncryptedType)


def _mask_uri(value: Any) -> str:
    from superset.constants import PASSWORD_MASK
    from superset.databases.utils import make_url_safe

    try:
        url = make_url_safe(str(value))
    except Exception:  # noqa: BLE001
        return REDACTED
    return str(url.set(password=PASSWORD_MASK)) if url.password else str(value)


def _value(name: str, column: Any, value: Any) -> Any:
    """A column value as stored in details: redacted, JSON-safe, cut to length."""
    if value is None:
        return None
    if _is_sensitive(name, column):
        return REDACTED
    if name == "sqlalchemy_uri":
        value = _mask_uri(value)
    return _cut_long(_plain(value))


def _plain(value: Any) -> Any:
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bytes):
        return f"<{len(value)} bytes sha256:{hashlib.sha256(value).hexdigest()}>"
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if not isinstance(value, str):
        try:
            value = json.dumps(value, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            value = str(value)
    return value


def _cut_long(value: Any) -> Any:
    if not isinstance(value, str) or len(value) <= MAX_VALUE_CHARS:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"{value[:MAX_VALUE_CHARS]}... [cut: {len(value)} chars, sha256:{digest}]"


def _column_changes(obj: Any, op: str, table: str) -> dict[str, Any]:
    insp = sa_inspect(obj)
    skip = NOISE_COLUMNS | (USER_NOISE_COLUMNS if table == "ab_user" else frozenset())
    changes: dict[str, Any] = {}
    for attr in insp.mapper.column_attrs:
        name = attr.key
        if name in skip:
            continue
        column = attr.columns[0]
        if op == "update":
            hist = insp.attrs[name].history
            if not hist.added and not hist.deleted:
                continue
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0] if hist.added else None
            if old == new or (not hist.deleted and new is None):
                continue
            changes[name] = {
                "old": _value(name, column, old),
                "new": _value(name, column, new),
            }
        else:
            value = insp.dict.get(name)
            if value is not None:
                changes[name] = _value(name, column, value)
    return changes


@dataclass
class _Pair:
    """One row added to or removed from a many-to-many table."""

    owner: Any  # the object whose relationship changed
    rel: RelationshipProperty
    other: Any
    op: str  # "added" | "removed"


def _collect_pairs(obj: Any, pairs: dict[tuple[Any, ...], _Pair]) -> None:
    insp = sa_inspect(obj)
    for rel in insp.mapper.relationships:
        if rel.secondary is None:
            continue
        hist = insp.attrs[rel.key].history
        # History fields are None, not empty, for an attribute without history.
        for op, items in (("added", hist.added), ("removed", hist.deleted)):
            for other in items or ():
                if other is None:
                    continue
                ends = frozenset(
                    {
                        (_table_name(obj), str(_pk(obj))),
                        (_table_name(other), str(_pk(other))),
                    }
                )
                key = (rel.secondary.name, ends, op)
                # The same row shows up on both sides of a backref; keep the side that
                # declared the relationship.
                if key not in pairs or rel.backref is not None:
                    pairs[key] = _Pair(obj, rel, other, op)


# Many-to-many tables with their own events: secondary -> (owner table, category,
# action stem, details key). Pairs are grouped per owner row.
_SECURITY_LINKS = {
    "ab_user_role": ("ab_user", "role", "role", "roles"),
    "ab_permission_view_role": ("ab_role", "role", "role.permission", "permissions"),
    "ab_group_role": ("ab_group", "group", "group.role", "roles"),
    "ab_user_group": ("ab_user", "user", "user.group", "groups"),
}
_LINK_ACTIONS = {
    ("role", "added"): "role.assign",
    ("role", "removed"): "role.revoke",
    ("role.permission", "added"): "role.permission_add",
    ("role.permission", "removed"): "role.permission_remove",
    ("group.role", "added"): "group.role_assign",
    ("group.role", "removed"): "group.role_revoke",
    ("user.group", "added"): "user.group_add",
    ("user.group", "removed"): "user.group_remove",
}


def _sql_dml_record(obj: Any) -> Optional[dict[str, Any]]:
    """data.sql_dml for a SQL Lab query that finished and writes to its database."""
    insp = sa_inspect(obj)
    status_hist = insp.attrs["status"].history
    status = str(getattr(obj.status, "value", obj.status) or "")
    if not status_hist.added or status not in {
        "success",
        "failed",
        "stopped",
        "timed_out",
    }:
        return None
    sql = obj.executed_sql or obj.sql or ""
    try:
        from superset.sql.parse import SQLScript

        if not SQLScript(sql, obj.database.db_engine_spec.engine).has_mutation():
            return None
    except Exception:  # noqa: BLE001 -- unparsable SQL is rejected by Superset itself
        return None
    # SQL Lab runs statements in a worker thread under a synthetic request ("GET /"),
    # so the client address is not known here; the query's owner is.
    overrides: dict[str, Any] = {"request": f"SQL Lab query {obj.client_id}"}
    from flask import has_request_context

    if not has_request_context() and obj.user_id:
        overrides.update(actor_id=obj.user_id, actor_username=f"user#{obj.user_id}")
    return _record(
        "data",
        "data.sql_dml",
        outcome="success" if status == "success" else "failure",
        target_type="dbs",
        target_id=obj.database_id,
        target_name=getattr(obj.database, "database_name", None),
        details={
            "query_id": obj.id,
            "status": status,
            "catalog": obj.catalog,
            "schema": obj.schema,
            "sql": _value("sql", None, sql),
            "error": _value("error_message", None, obj.error_message),
        },
        overrides=overrides,
    )


def _describe(
    session: Session,
) -> tuple[
    list[dict[str, Any]], dict[tuple[Any, ...], _Pair], dict[int, dict[str, Any]]
]:
    """Pass over the flushed objects: SQL Lab records, M2M pairs, per-object events."""
    records: list[dict[str, Any]] = []
    pairs: dict[tuple[Any, ...], _Pair] = {}
    events: dict[int, dict[str, Any]] = {}  # id(obj) -> event under construction
    ops = (
        [("create", o) for o in session.new]
        + [("update", o) for o in session.dirty]
        + [("delete", o) for o in session.deleted]
    )
    for op, obj in ops:
        table = "unknown"
        try:
            table = _table_name(obj)
            if table == "query":
                if op != "delete" and (record := _sql_dml_record(obj)) is not None:
                    records.append(record)
                continue
            if table in EXCLUDED_TABLES:
                continue
            _collect_pairs(obj, pairs)
            event_ = {"obj": obj, "op": op, "table": table}
            changes = _column_changes(obj, op, table)
            if op != "update" or changes:
                event_["changes"] = changes
            events[id(obj)] = event_
        except Exception:  # noqa: BLE001 -- never lose the event, only its details
            logger.exception("audit: could not describe a change")
            events[id(obj)] = {"obj": obj, "op": op, "table": table, "changes": {}}
    return records, pairs, events


def _link_records(
    pairs: dict[tuple[Any, ...], _Pair], events: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Security links become their own events; other M2M changes join their owner's."""
    grouped: dict[tuple[Any, ...], list[Any]] = {}
    for pair in pairs.values():
        secondary = pair.rel.secondary.name
        if secondary in _SECURITY_LINKS:
            owner, other = pair.owner, pair.other
            if _table_name(owner) != _SECURITY_LINKS[secondary][0]:
                owner, other = other, owner
            group = grouped.setdefault((secondary, id(owner), pair.op), [owner])
            group.append(_ref(other))
            continue
        ev = events.setdefault(
            id(pair.owner),
            {"obj": pair.owner, "op": "update", "table": _table_name(pair.owner)},
        )
        rel = ev.setdefault("relationships", {}).setdefault(
            pair.rel.key, {"added": [], "removed": []}
        )
        rel[pair.op].append(_ref(pair.other))

    records = []
    for (secondary, _, op), (owner, *refs) in grouped.items():
        owner_table, category, stem, key = _SECURITY_LINKS[secondary]
        records.append(
            _record(
                category,
                _LINK_ACTIONS[(stem, op)],
                target_type=owner_table,
                target_id=_pk(owner),
                target_name=_label(owner),
                details={key: sorted(refs)},
            )
        )
    return records


def _object_record(ev: dict[str, Any]) -> Optional[dict[str, Any]]:
    if "changes" not in ev and "relationships" not in ev:
        return None  # dirty, but nothing that matters changed
    table = ev["table"]
    category = SECURITY_CATEGORIES.get(table, "data")
    details: dict[str, Any] = {}
    if ev.get("changes"):
        details["changes" if ev["op"] == "update" else "values"] = ev["changes"]
    if ev.get("relationships"):
        details["relationships"] = ev["relationships"]
    return _record(
        category,
        f"{category}.{ev['op']}",
        target_type=table,
        target_id=_pk(ev["obj"]),
        target_name=_label(ev["obj"]),
        details=details,
    )


def _build_records(session: Session) -> list[dict[str, Any]]:
    records, pairs, events = _describe(session)
    records += _link_records(pairs, events)
    records += [r for ev in events.values() if (r := _object_record(ev)) is not None]
    return records


def _is_metadata(conn: Connection) -> bool:
    from flask import has_app_context

    if not has_app_context():
        return False
    from superset.extensions import db

    return str(conn.engine.url) == str(db.engine.url)


def _after_flush(session: Session, _flush_context: Any) -> None:
    if not _state.initialised or not (session.new or session.dirty or session.deleted):
        return
    conn = session.connection()
    if not _is_metadata(conn):
        return
    records = _build_records(session)
    if not records:
        return
    if _state.db_enabled:
        _insert(
            conn, records
        )  # fails the flush, and the change, if it cannot be written
    session.info.setdefault(_PENDING, []).append(
        (session.get_nested_transaction(), records)
    )


def _after_commit(session: Session) -> None:
    if pending := session.info.pop(_PENDING, None):
        _emit_stdout([r for _, records in pending for r in records])


def _after_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    """Forget records that a rollback undid: everything, or one savepoint's worth."""
    pending = session.info.get(_PENDING)
    if not pending:
        return
    if not previous.nested:
        session.info.pop(_PENDING, None)
        return

    def inside(txn: Optional[SessionTransaction]) -> bool:
        while txn is not None:
            if txn is previous:
                return True
            txn = txn.parent
        return False

    session.info[_PENDING] = [(t, r) for t, r in pending if not inside(t)]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(old_secrets: Optional[list[str]] = None) -> int:
    """Recompute every row's HMAC. Returns the number of rows that do not match.

    Rows are checked against the current key, the key derived from the current
    SECRET_KEY, and each of `old_secrets` (previous LAURETTA_AUDIT_HMAC_KEY or
    SUPERSET_SECRET_KEY values), so rows written before a key rotation still verify.
    """
    from flask import current_app

    from superset.extensions import db

    candidates = [_state.key, _derived_key(str(current_app.config["SECRET_KEY"]))]
    for secret in old_secrets or []:
        candidates += [secret.encode(), _derived_key(secret)]
    keys = {_key_id(key): key for key in candidates}
    ok = bad = unknown = 0
    first_bad: list[int] = []
    gaps: list[str] = []
    missing = 0
    previous_id: Optional[int] = None
    with db.engine.connect() as conn:
        rows = conn.execution_options(stream_results=True).execute(
            sa.select(audit_table).order_by(audit_table.c.id)
        )
        for row in rows:
            record = dict(row._mapping)
            if previous_id is not None and record["id"] > previous_id + 1:
                missing += record["id"] - previous_id - 1
                if len(gaps) < 20:
                    gaps.append(f"{previous_id + 1}-{record['id'] - 1}")
            previous_id = record["id"]
            key = keys.get(record["key_id"])
            if key is None:
                unknown += 1
            elif hmac.compare_digest(_sign(record, key), record["row_hmac"]):
                ok += 1
            else:
                bad += 1
                if len(first_bad) < 20:
                    first_bad.append(record["id"])
    print(f"audit verify: {ok} ok, {bad} TAMPERED, {unknown} signed with another key")
    if first_bad:
        print(f"audit verify: first tampered ids: {first_bad}")
    if missing:
        # A rolled-back change also leaves a gap: its audit row was rolled back with
        # it. Compare these ranges with the stdout/SIEM copy, which never has them.
        print(
            f"audit verify: {missing} ids absent (rolled back or deleted), "
            f"first ranges: {gaps}"
        )
    return bad


def _main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"create-table", "verify"}:
        print("usage: python -m lauretta_audit {create-table|verify}", file=sys.stderr)
        return 2
    if argv[0] == "create-table":
        os.environ[INIT_ENV] = "1"
    from superset.app import create_app

    # The app's own copy of this module (imported by the config), not __main__.
    import lauretta_audit

    app = create_app()
    with app.app_context():
        if argv[0] == "create-table":
            lauretta_audit.create_table()
            return 0
        old = [k for k in os.environ.get(OLD_KEYS_ENV, "").split(",") if k.strip()]
        return 1 if lauretta_audit.verify(old) else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
