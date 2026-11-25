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

Every event is written to three places:
  * the append-only table `lauretta_audit_log` in the metadata database,
  * one JSON line on stdout (logger `lauretta.audit`), and
  * the same line appended to LAURETTA_AUDIT_FILE (default
    /app/superset_home/audit/audit.jsonl), the file a SIEM agent watches.

Captured events (see lauretta/docs/12-audit-trail.md for the full list):
  * auth.login (success and failure, with the reason; method "machine" for the
    browser session Superset opens as a user for reports, alerts and thumbnails) and
    auth.logout: an explicit logout, or an idle timeout found by the session sweep
    (lauretta_audit_sessions)
  * user.create / user.update / user.delete
  * role.assign / role.revoke (user <-> role), role.permission_add / _remove,
    role.create / role.update / role.delete, and the group equivalents
  * data.create / data.update / data.delete for every other ORM-mapped table in the
    metadata database (dashboards, charts, datasets, databases, ...), minus the
    bookkeeping tables in EXCLUDED_TABLES
  * <category>.bulk_create / .bulk_update / .bulk_delete: a statement that changes
    many rows at once (Query.update/delete, session.execute(insert/update/delete))
  * <category>.create / .update for rows written with Session.bulk_insert_mappings /
    bulk_update_mappings, which skip the unit of work (dataset columns and metrics)
  * role.permission_remove for permissions Superset deletes with raw SQL when a
    dataset or database is deleted or changed
  * data.sql_dml: a SQL Lab statement that writes to a data source, or a Jinja
    templated one that may (its rendered script is not stored)
  * data.file_upload: a floor-map image upload
  * access.request: every request by a privileged account (LAURETTA_AUDIT_PRIVILEGED_
    ROLES, default Admin): what it viewed, queried, downloaded or exported
  * auth.repeated_failure: one username or client address failing to log in
    LAURETTA_AUDIT_FAILED_LOGIN_THRESHOLD times within the window
  * audit.report / audit.verify: somebody read the audit trail with this tool

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
  python -m lauretta_audit sweep-sessions # record idle timeouts now (cron; optional)
  python -m lauretta_audit report [--from DATE] [--to DATE] [--user NAME]
      [--category C] [--action A] [--out FILE]   # CSV report, with a seal check
"""

from __future__ import annotations

import argparse
import csv
import enum
import fcntl
import functools
import getpass
import hashlib
import hmac

# The standard library on purpose: the row HMAC is computed over this exact
# serialisation, so it must not change with superset.utils.json.
import json  # noqa: TID251
import logging
import os
import re
import secrets
import socket
import sys
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional, TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction

if TYPE_CHECKING:
    from flask import Flask
    from sqlalchemy.orm.relationships import RelationshipProperty

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("lauretta.audit")

TABLE_NAME = "lauretta_audit_log"
TRIGGER_FUNCTION = "lauretta_audit_log_append_only"
# Working state, not audit trail: one row per browser login, so that an idle timeout
# (a session nobody ends) can be recorded as a logout.
SESSIONS_TABLE = "lauretta_audit_sessions"
SESSION_SID_KEY = "_lauretta_audit_sid"
SESSION_SEEN_KEY = "_lauretta_audit_seen"
# Defaults; each can be overridden in the Superset config under the same name.
#   TOUCH: how often (seconds) a request refreshes the session's last_seen
#   SWEEP: how often (seconds) each worker looks for timed-out sessions
LAURETTA_AUDIT_TOUCH_SECONDS = 60
LAURETTA_AUDIT_SWEEP_SECONDS = 60
#   FAILED_LOGIN_*: N failed logins for one username or one client address within
#   the window raise one auth.repeated_failure (then at most one per window)
LAURETTA_AUDIT_FAILED_LOGIN_THRESHOLD = 5
LAURETTA_AUDIT_FAILED_LOGIN_WINDOW_SECONDS = 600
# Roles whose holders have every request recorded (access.request). A list in config.
LAURETTA_AUDIT_PRIVILEGED_ROLES = ("Admin",)
# The SIEM file. Unset: DEFAULT_FILE; empty: no file.
FILE_ENV = "LAURETTA_AUDIT_FILE"
DEFAULT_FILE = "/app/superset_home/audit/audit.jsonl"
# Size rotation of that file: at FILE_MAX_MB it becomes audit.jsonl.1 (the older ones
# move up to .2, .3, ...) and the oldest beyond FILE_BACKUPS is deleted.
FILE_MAX_MB_ENV = "LAURETTA_AUDIT_FILE_MAX_MB"
FILE_BACKUPS_ENV = "LAURETTA_AUDIT_FILE_BACKUPS"
DEFAULT_FILE_MAX_MB = 50
DEFAULT_FILE_BACKUPS = 20
# Requests by privileged accounts that are not recorded: assets, probes, the frontend's
# own telemetry and polling. (method or "*", path prefix)
QUIET_REQUESTS = (
    ("*", "/static/"),
    ("*", "/health"),
    ("*", "/ready"),
    ("*", "/favicon"),
    ("*", "/api/v1/log/"),
    ("POST", "/login/"),  # the login itself is auth.login
    ("GET", "/api/v1/me/"),
    ("GET", "/api/v1/security/csrf_token/"),
    ("GET", "/api/v1/async_event/"),
)
# Bulk statements: at most this many affected primary keys are listed.
MAX_BULK_IDS = 1000
JWT_LOGIN_PATH = "/api/v1/security/login"

# Set by the init scripts (docker/docker-init.sh, docker/docker-init-ha.sh): the CLI
# commands they run before `create-table` must not fail on the missing table.
INIT_ENV = "LAURETTA_AUDIT_INIT"
KEY_ENV = "LAURETTA_AUDIT_HMAC_KEY"
# The `node` column. In a container the hostname is the container id, which changes on
# every recreate and does not tell HA nodes apart; HA mode sets
# DATABASE_APPLICATION_NAME per node (raib-ha: superset-ha-<node>), so that comes next.
NODE_ENVS = ("LAURETTA_AUDIT_NODE", "DATABASE_APPLICATION_NAME")
# `verify` only: comma-separated earlier LAURETTA_AUDIT_HMAC_KEY / SUPERSET_SECRET_KEY.
OLD_KEYS_ENV = "LAURETTA_AUDIT_OLD_KEYS"

# Longest stored value (characters) before it is cut and replaced by a hash.
MAX_VALUE_CHARS = 1000

# Metadata tables whose rows are not business data: request bookkeeping, caches,
# SQL Lab editor state, and the permission catalogue `superset init` maintains.
EXCLUDED_TABLES = frozenset(
    {
        TABLE_NAME,
        SESSIONS_TABLE,
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
SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "private_key",
)
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


sessions_table = sa.Table(
    SESSIONS_TABLE,
    _metadata,
    sa.Column("sid", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.Integer()),
    sa.Column("username", sa.String(256)),
    sa.Column("ip_address", sa.String(64)),
    sa.Column("user_agent", sa.String(512)),
    sa.Column("node", sa.String(128)),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    sa.Column("end_reason", sa.String(32)),
    sa.Index("ix_lauretta_audit_sessions_open", "ended_at", "last_seen"),
)


def _node_name() -> str:
    for name in NODE_ENVS:
        if value := os.environ.get(name, "").strip():
            return value
    return socket.gethostname()


@dataclass
class _State:
    initialised: bool = False
    db_enabled: bool = False
    key: bytes = b""
    key_id: str = ""
    node: str = field(default_factory=_node_name)
    tracking: bool = False  # SESSIONS_TABLE exists: idle timeouts are recorded
    last_sweep: float = 0.0
    file_path: Optional[str] = None  # the SIEM file; None: none
    file_warned: bool = False
    file_max_bytes: int = DEFAULT_FILE_MAX_MB * 1024 * 1024
    file_backups: int = DEFAULT_FILE_BACKUPS


_state = _State()

# session.info key: [(savepoint or None, [records])] written but not yet committed, by a
# session that owns its database transaction (only such a session's commit commits).
_PENDING = "lauretta_audit_pending"
# Connection.info key (per DBAPI connection): [(root transaction, [records])] written
# by a Session joined to a transaction it does not own (Superset's tag listeners,
# inside the owner's flush). The owner's flush in that same transaction adopts them.
# Any rollback on the connection drops them: a line may then be missing, but never
# printed for a change that did not commit.
_DEFERRED = "lauretta_audit_deferred"
# Connection.info key: [(savepoint names, [records])] queued in this transaction. A
# savepoint rolled back on the connection (also one the session did not open) empties
# the record lists it covered, which are the same lists the session will print.
_OPEN = "lauretta_audit_open"
# Connection.info key: [records] built inside a flush by Superset's own raw-SQL hooks
# (permission removals); the same flush's after_flush inserts and queues them.
_SIDE = "lauretta_audit_side"

# SQL Lab runs a query under a synthetic request; the client that submitted it is
# remembered here, per query client_id, from the request that created the Query row.
_SQL_CLIENTS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_SQL_CLIENTS_MAX = 2000


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
            if not _ldap_input_allowed(username, password):
                _audit_login(self, "ldap", username or "", None, "refused_input")
                return None
            # AD matches logins case-insensitively; one spelling keeps the account
            # FAB registers at the first login, and the audit trail, consistent.
            username = username.lower()
            user = super().auth_user_ldap(username, password)
            _audit_login(self, "ldap", username, user)
            return user

        def get_roles_from_keys(self, role_keys: list[str]) -> Any:
            # AD group DNs: compare in lauretta_ldap's normalised form, the form
            # AUTH_ROLES_MAPPING is stored in, so a group DN typed in another case
            # still maps to its roles.
            return super().get_roles_from_keys(_role_keys(self.auth_type, role_keys))

        def auth_user_oauth(self, userinfo: dict[str, Any]) -> Any:
            user = super().auth_user_oauth(userinfo)
            name = userinfo.get("username") or userinfo.get("email") or ""
            _audit_login(self, "oauth", str(name), user)
            return user

        def auth_user_remote_user(self, username: str) -> Any:
            user = super().auth_user_remote_user(username)
            _audit_login(self, "remote_user", username, user)
            return user

        def _delete_pvm_on_sqla_event(  # pylint: disable=too-many-arguments
            self,
            mapper: Any,
            connection: Connection,
            permission_name: Optional[str] = None,
            view_menu_name: Optional[str] = None,
            pvm: Any = None,
        ) -> None:
            # Superset removes the permission from every role with raw SQL, inside
            # the flush that deletes or changes the dataset/database: read who held
            # it first.
            if not pvm and permission_name and view_menu_name:
                pvm = self.find_permission_view_menu(permission_name, view_menu_name)
            holders = _permission_holders(connection, pvm) if pvm else []
            super()._delete_pvm_on_sqla_event(
                mapper,
                connection,
                permission_name=permission_name,
                view_menu_name=view_menu_name,
                pvm=pvm,
            )
            if holders:
                _stash_side(connection, _permission_removed_records(mapper, holders))

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
        inspector = sa_inspect(db.engine)
        missing = [
            t for t in (TABLE_NAME, SESSIONS_TABLE) if not inspector.has_table(t)
        ]
    if missing:
        if os.environ.get(INIT_ENV) != "1":
            raise RuntimeError(
                f"audit table(s) {', '.join(missing)} missing from the metadata "
                "database: run the init (docker/docker-init.sh, or "
                "docker/docker-init-ha.sh in HA mode), which creates them"
            )
        logger.warning("audit table(s) %s missing: stdout only", ", ".join(missing))
    _state.db_enabled = TABLE_NAME not in missing
    _state.tracking = SESSIONS_TABLE not in missing
    _state.file_path = os.environ.get(FILE_ENV, DEFAULT_FILE).strip() or None
    _state.file_max_bytes = _env_int(FILE_MAX_MB_ENV, DEFAULT_FILE_MAX_MB) * 1024 * 1024
    _state.file_backups = _env_int(FILE_BACKUPS_ENV, DEFAULT_FILE_BACKUPS)
    app.before_request(_touch_session)
    app.after_request(_audit_privileged)

    if not _state.initialised:
        from flask_login import user_logged_in, user_logged_out

        _listen()
        user_logged_in.connect(_on_login)
        user_logged_out.connect(_on_logout)
        _state.initialised = True
    logger.info("audit trail active (table: %s)", "no" if missing else "yes")


def _listen() -> None:
    """The ORM and connection listeners behind the stdout rule: a line is printed only
    after the database transaction that holds its row has committed."""
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_soft_rollback", _after_soft_rollback)
    event.listen(Session, "after_transaction_end", _after_transaction_end)
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    _patch_bulk_mappings()
    # Imported here, not on the first flush: its import registers Session listeners,
    # which must not happen while SQLAlchemy iterates them.
    import sqlalchemy_utils  # noqa: F401

    event.listen(sa.engine.Engine, "commit", _on_connection_end)
    event.listen(sa.engine.Engine, "rollback", _on_connection_rollback)
    event.listen(sa.engine.Engine, "rollback_savepoint", _on_savepoint_rollback)
    event.listen(sa.pool.Pool, "checkin", _on_checkin)


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
        if not sa_inspect(conn).has_table(SESSIONS_TABLE):
            sessions_table.create(conn)
            print(f"audit: created table {SESSIONS_TABLE}")
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
    context: Optional[dict[str, Any]] = None,
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
    # `context` replaces the current request's actor and client entirely (an idle
    # timeout found during somebody else's request); `overrides` only fills in.
    record.update(_context() if context is None else context)
    # Who and where, when the request context does not tell (logins, SQL Lab threads).
    if overrides:
        record.update({k: v for k, v in overrides.items() if v is not None})
    record["actor_username"] = _cut(record.get("actor_username"), 256)
    record["row_hmac"] = _sign(record, _state.key)
    return record


def _emit_stdout(records: list[dict[str, Any]]) -> None:
    """The two log copies, stdout and the SIEM file, once the rows have committed."""
    lines = []
    for record in records:
        line = dict(record)
        line["event_time"] = _iso(record["event_time"])
        line["details"] = json.loads(record["details"])
        lines.append(
            json.dumps({"lauretta_audit": 1, **line}, ensure_ascii=False, default=str)
        )
    for text in lines:
        audit_logger.info(text)
    _append_file(lines)


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.environ.get(name, "").strip() or default), 1)
    except ValueError:
        logger.warning("audit: %s is not a number; using %d", name, default)
        return default


def _append_file(lines: list[str]) -> None:
    """Append to the SIEM file: one write under an exclusive lock, so lines from
    several workers never interleave, then rotate it if it has grown past the limit.
    A failure is logged once per process, never raised: the table and stdout still
    have the event."""
    path = _state.file_path
    if not path or not lines:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", mode=0o750, exist_ok=True)
        fd = _open_locked(path)
        try:
            os.write(fd, ("\n".join(lines) + "\n").encode())
            if os.fstat(fd).st_size >= _state.file_max_bytes:
                _rotate(path, _state.file_backups)
        finally:
            os.close(fd)  # also releases the lock
    except OSError as exc:
        if not _state.file_warned:
            _state.file_warned = True
            logger.warning("audit: cannot append to %s: %s", path, exc)


def _open_locked(path: str) -> int:
    """The current file at `path`, opened for append and locked. Another worker may
    rotate it while this one waits for the lock; then the lock is on a file that is no
    longer `path`, so open again: lines always land in the current file."""
    for _ in range(10):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            current = os.stat(path)
        except FileNotFoundError:
            current = None
        mine = os.fstat(fd)
        if current and (current.st_ino, current.st_dev) == (mine.st_ino, mine.st_dev):
            return fd
        os.close(fd)
    raise OSError(f"{path} keeps being rotated away")


def _rotate(path: str, backups: int) -> None:
    """audit.jsonl -> .1 -> .2 ... -> .<backups>, the oldest beyond that deleted.
    Called with the lock on `path` held, so only one worker rotates. A new, empty
    file takes its place at once, so the path always exists for the SIEM agent, which
    (following the file by inode) reads the rest of the old one, then the new one."""
    oldest = f"{path}.{backups}"
    if os.path.exists(oldest):
        os.remove(oldest)
    for n in range(backups - 1, 0, -1):
        if os.path.exists(f"{path}.{n}"):
            os.rename(f"{path}.{n}", f"{path}.{n + 1}")
    os.rename(path, f"{path}.1")
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o640))


def _insert(conn: Connection, records: list[dict[str, Any]]) -> None:
    for record in records:
        result = conn.execute(audit_table.insert().values(**record))
        record["id"] = result.inserted_primary_key[0]


def record_event(
    category: str,
    action: str,
    *,
    also: Optional[Callable[[Connection, dict[str, Any]], None]] = None,
    **kwargs: Any,
) -> None:
    """Write one event in its own transaction (logins, logouts, file uploads).

    `also(conn, record)` runs in that transaction first (session tracking). A failed
    database write is logged and does not fail the caller; the stdout line is still
    printed.
    """
    if not _state.initialised:
        return
    record = _record(category, action, **kwargs)
    if _state.db_enabled:
        try:
            from superset.extensions import db

            with db.engine.begin() as conn:
                if also is not None:
                    also(conn, record)
                _insert(conn, [record])
        except Exception:  # noqa: BLE001
            logger.exception("audit: could not write %s to %s", action, TABLE_NAME)
    _emit_stdout([record])


# ---------------------------------------------------------------------------
# Logins and logouts
# ---------------------------------------------------------------------------
def _role_keys(auth_type: int, role_keys: list[str]) -> list[str]:
    """Under LDAP, the group DNs in lauretta_ldap's normalised form."""
    from flask_appbuilder.security.manager import AUTH_LDAP

    if auth_type != AUTH_LDAP:
        return role_keys
    import lauretta_ldap

    return [lauretta_ldap.normalize_dn(key) for key in role_keys]


def _ldap_input_allowed(username: str, password: str) -> bool:
    """lauretta_ldap.username_allowed with the pattern from ldap.json."""
    import lauretta_ldap
    from flask import current_app

    pattern = current_app.config.get(
        "LAURETTA_LDAP_USERNAME_PATTERN", lauretta_ldap.DEFAULT_USERNAME_PATTERN
    )
    return lauretta_ldap.username_allowed(username, password, pattern)


def _audit_login(
    sm: Any, method: str, username: str, user: Any, reason: Optional[str] = None
) -> None:
    try:
        if user:
            record_event(
                "auth",
                "auth.login",
                target_type="ab_user",
                target_id=user.id,
                target_name=user.username,
                details={"method": method, **_login_session_details()},
                overrides={"actor_id": user.id, "actor_username": user.username},
            )
            return
        existing = sm.find_user(username=username) if username else None
        if reason is None:  # else the caller refused the attempt itself
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
        from flask import has_request_context, request

        ip = request.remote_addr if has_request_context() else None
        check_repeated_failures(username or None, ip)
    except Exception:  # noqa: BLE001 -- auditing must never break a login
        logger.exception("audit: could not record a login")


def check_repeated_failures(
    username: Optional[str],
    ip_address: Optional[str],
    engine: Optional[sa.engine.Engine] = None,
    now: Optional[datetime] = None,
) -> int:
    """auth.repeated_failure when a username, or a client address (a password spray
    across usernames), has failed THRESHOLD times within WINDOW seconds; at most one
    per key per window. Returns how many it recorded.

    Reads the failures back from the audit table, so it counts across workers and HA
    nodes. On PostgreSQL an advisory lock serialises the check, so two nodes never
    raise the same one twice.
    """
    if not _state.db_enabled or not (username or ip_address):
        return 0
    if engine is None:
        from superset.extensions import db

        engine = db.engine
    threshold = _setting("LAURETTA_AUDIT_FAILED_LOGIN_THRESHOLD")
    window = _setting("LAURETTA_AUDIT_FAILED_LOGIN_WINDOW_SECONDS")
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(seconds=window)
    t = audit_table
    records: list[dict[str, Any]] = []
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:n))"),
                {"n": "lauretta_audit_repeated_failure"},
            )
        for kind, column, value in (
            ("username", t.c.actor_username, username),
            ("ip_address", t.c.ip_address, ip_address),
        ):
            if not value:
                continue
            failed = sa.and_(
                t.c.action == "auth.login",
                t.c.outcome == "failure",
                t.c.event_time >= since,
                column == value,
            )
            count, first = conn.execute(
                sa.select(sa.func.count(), sa.func.min(t.c.event_time)).where(failed)
            ).one()
            if count < threshold:
                continue
            raised = conn.execute(
                sa.select(sa.func.count()).where(
                    t.c.action == "auth.repeated_failure",
                    t.c.event_time >= since,
                    t.c.target_type == kind,
                    t.c.target_name == value,
                )
            ).scalar()
            if raised:
                continue
            details: dict[str, Any] = {
                "failures": count,
                "threshold": threshold,
                "window_seconds": window,
                "first_failure": _iso(_aware(first)),
            }
            if kind == "ip_address":
                names = conn.execute(
                    sa.select(t.c.actor_username).distinct().where(failed).limit(20)
                ).scalars()
                details["usernames"] = sorted(n for n in names if n)
            records.append(
                _record(
                    "auth",
                    "auth.repeated_failure",
                    outcome="failure",
                    target_type=kind,
                    target_name=value,
                    details=details,
                    overrides={"event_time": now},
                )
            )
        if records:
            _insert(conn, records)
    _emit_stdout(records)
    return len(records)


# ---------------------------------------------------------------------------
# Privileged accounts: every request
# ---------------------------------------------------------------------------
def _privileged_roles() -> frozenset[str]:
    from flask import current_app

    roles = current_app.config.get(
        "LAURETTA_AUDIT_PRIVILEGED_ROLES", LAURETTA_AUDIT_PRIVILEGED_ROLES
    )
    return frozenset(roles or ())


def _quiet(method: str, path: str) -> bool:
    return any(
        (m == "*" or m == method) and path.startswith(prefix)
        for m, prefix in QUIET_REQUESTS
    )


def _request_details(request: Any) -> dict[str, Any]:
    """What a request asked for: query arguments, and for SQL Lab and chart data the
    SQL or the data format (a download)."""
    details: dict[str, Any] = {"method": request.method, "path": request.path}
    args = {
        k: (REDACTED if _sensitive_name(k) else _cut_long(v))
        for k, v in request.args.items()
    }
    if args:
        details["args"] = args
    body = _request_body(request)
    if request.path.startswith("/api/v1/sqllab/"):
        for key in ("sql", "database_id", "catalog", "schema"):
            if body.get(key) is not None:
                details[key] = _cut_long(_redact(body[key]))
    if "/chart/data" in request.path:
        for key in ("result_format", "result_type", "datasource"):
            if body.get(key):
                details[key] = body[key]
    return details


def _request_body(request: Any) -> dict[str, Any]:
    """The JSON body, or a form's form_data field (chart downloads); {} otherwise."""
    body: Any = request.get_json(silent=True) if request.is_json else None
    if body is None and request.form.get("form_data"):
        try:
            body = json.loads(request.form["form_data"])
        except ValueError:
            body = None
    return body if isinstance(body, dict) else {}


def _audit_privileged(response: Any) -> Any:
    """after_request: one access.request per request by a privileged account, so what
    an administrator viewed, queried, downloaded or exported is on record (changes
    also get their own rows). Never fails the request."""
    try:
        from flask import request
        from flask_login import current_user

        if not _state.initialised or not getattr(
            current_user, "is_authenticated", False
        ):
            return response
        if _quiet(request.method, request.path):
            return response
        held = {r.name for r in getattr(current_user, "roles", []) or []}
        roles = sorted(held & _privileged_roles())
        if not roles:
            return response
        status = int(getattr(response, "status_code", 0) or 0)
        record_event(
            "access",
            "access.request",
            outcome="success" if status < 400 else "failure",
            target_type=request.endpoint,
            details={**_request_details(request), "status": status, "roles": roles},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit: could not record a privileged request: %s", exc)
    return response


def _login_session_details() -> dict[str, Any]:
    """What the login opens: a browser session (its tracking id, picked up by
    _on_login) or JWT tokens, which have no logout and simply expire."""
    from flask import current_app, g, has_request_context, request

    if not has_request_context():
        return {}
    if request.path.rstrip("/") == JWT_LOGIN_PATH:
        now = datetime.now(timezone.utc)
        details: dict[str, Any] = {"session": "jwt"}
        body = request.get_json(silent=True) or {}
        for kind, key, default in (
            ("access", "JWT_ACCESS_TOKEN_EXPIRES", timedelta(minutes=15)),
            ("refresh", "JWT_REFRESH_TOKEN_EXPIRES", timedelta(days=30)),
        ):
            if kind == "refresh" and not body.get("refresh"):
                continue
            lifetime = current_app.config.get(key, default)
            if isinstance(lifetime, (int, float)) and not isinstance(lifetime, bool):
                lifetime = timedelta(seconds=lifetime)
            details[f"{kind}_token_expires"] = (
                _iso(now + lifetime) if isinstance(lifetime, timedelta) else "never"
            )
        return details
    sid = secrets.token_hex(16)
    g.lauretta_audit_sid = sid
    return {"session": sid}


def _on_logout(sender: Any, user: Any = None, **_: Any) -> None:
    try:
        if user is None or not getattr(user, "is_authenticated", False):
            return
        from flask import has_request_context, session

        sid = None
        if has_request_context():
            sid = session.pop(SESSION_SID_KEY, None)
            session.pop(SESSION_SEEN_KEY, None)

        def close(conn: Connection, record: dict[str, Any]) -> None:
            if sid and _state.tracking:
                conn.execute(
                    sessions_table.update()
                    .where(
                        sessions_table.c.sid == sid, sessions_table.c.ended_at.is_(None)
                    )
                    .values(ended_at=record["event_time"], end_reason="logout")
                )

        record_event(
            "auth",
            "auth.logout",
            also=close,
            target_type="ab_user",
            target_id=user.id,
            target_name=user.username,
            details={"reason": "logout", "session": sid},
            overrides={"actor_id": user.id, "actor_username": user.username},
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit: could not record a logout")


# ---------------------------------------------------------------------------
# Browser sessions and idle timeouts
# ---------------------------------------------------------------------------
def _setting(name: str) -> int:
    from flask import current_app, has_app_context

    default = int(globals()[name])
    if not has_app_context():
        return default
    return int(current_app.config.get(name, default))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _start_tracking(user: Any, sid: Optional[str] = None) -> None:
    """A row for this browser session; its id lives in the (signed) Flask session."""
    from flask import request, session

    from superset.extensions import db

    sid = sid or secrets.token_hex(16)
    now = datetime.now(timezone.utc)
    with db.engine.begin() as conn:
        conn.execute(
            sessions_table.insert().values(
                sid=sid,
                user_id=user.id,
                username=_cut(user.username, 256),
                ip_address=_cut(request.remote_addr, 64),
                user_agent=_cut(request.user_agent.string or None, 512),
                node=_cut(_state.node, 128),
                started_at=now,
                last_seen=now,
            )
        )
    session[SESSION_SID_KEY] = sid
    session[SESSION_SEEN_KEY] = now.timestamp()


def _on_login(sender: Any, user: Any = None, **_: Any) -> None:
    """flask-login's user_logged_in: a browser login (the JWT API never sends it).

    A login through one of the auth methods above has already been recorded and left
    its session id in `g`. Without one, login_user() was called directly: for a real
    user that is Superset's machine auth (superset/utils/machine_auth.py), which opens
    a browser session as a user to render reports, alerts and thumbnails. It is
    recorded here, so its later logout (an idle timeout) has a matching login.
    Embedded and public dashboards log in AnonymousUserMixin, which is skipped.
    """
    try:
        if user is None or not getattr(user, "is_authenticated", False):
            return
        from flask import g

        # Popped: the next login_user() in this app context (several report
        # screenshots in one Celery task) must not reuse it.
        sid = g.pop("lauretta_audit_sid", None)
        if sid is None:
            sid = secrets.token_hex(16)
            record_event(
                "auth",
                "auth.login",
                target_type="ab_user",
                target_id=user.id,
                target_name=user.username,
                details={"method": "machine", "session": sid},
                overrides={"actor_id": user.id, "actor_username": user.username},
            )
        if _state.tracking:
            _start_tracking(user, sid)
    except Exception:  # noqa: BLE001 -- tracking must never break a login
        logger.exception("audit: could not record or track a login")


def _touch_session() -> None:
    """before_request: keep last_seen to within TOUCH seconds, and sweep now and then.

    Only browser sessions (flask-login's _user_id in the cookie session); a session
    started before this code ran, or one a sweep has already closed, gets a new row.
    """
    if not _state.tracking:
        return
    try:
        from flask import session
        from flask_login import current_user

        now = datetime.now(timezone.utc)
        if "_user_id" in session and current_user.is_authenticated:
            sid = session.get(SESSION_SID_KEY)
            seen = session.get(SESSION_SEEN_KEY) or 0
            if not sid:
                _start_tracking(current_user)
            elif now.timestamp() - seen >= _setting("LAURETTA_AUDIT_TOUCH_SECONDS"):
                _touch(sid, now)
        if time.monotonic() - _state.last_sweep >= _setting(
            "LAURETTA_AUDIT_SWEEP_SECONDS"
        ):
            _state.last_sweep = time.monotonic()
            sweep_sessions()
    except Exception as exc:  # noqa: BLE001 -- never fail a request over tracking
        # Expected while the metadata database is read-only (an HA failover): one line,
        # not a traceback per worker per minute.
        logger.warning("audit: session tracking failed: %s", exc)


def _touch(sid: str, now: datetime) -> None:
    from flask import session
    from flask_login import current_user

    from superset.extensions import db

    with db.engine.begin() as conn:
        updated = conn.execute(
            sessions_table.update()
            .where(sessions_table.c.sid == sid, sessions_table.c.ended_at.is_(None))
            .values(last_seen=now)
        ).rowcount
    if updated:
        session[SESSION_SEEN_KEY] = now.timestamp()
        return
    # Closed by a sweep while the cookie was still valid (clock skew between nodes, or
    # a lifetime raised without a restart everywhere): keep tracking under a new id.
    logger.warning("audit: session %s was closed but is still in use", sid[:8])
    _start_tracking(current_user)


def sweep_sessions(
    engine: Optional[sa.engine.Engine] = None,
    lifetime: Optional[timedelta] = None,
    now: Optional[datetime] = None,
) -> int:
    """Record an auth.logout (reason idle_timeout) for every browser session idle for
    longer than the session lifetime. Returns how many it recorded.

    A session counts as ended at last_seen + lifetime; its last request may have come
    up to TOUCH seconds after last_seen, so a session is only closed once that much
    more time (and a margin) has passed. Each row is claimed with a conditional UPDATE,
    so workers and nodes sweeping at the same time never record one twice, and the
    claim and its audit row commit together.
    """
    from flask import current_app

    if engine is None:
        from superset.extensions import db

        engine = db.engine
    lifetime = lifetime or current_app.permanent_session_lifetime
    now = now or datetime.now(timezone.utc)
    cutoff = (
        now
        - lifetime
        - timedelta(seconds=_setting("LAURETTA_AUDIT_TOUCH_SECONDS") + 30)
    )
    t = sessions_table
    records: list[dict[str, Any]] = []
    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(t)
            .where(t.c.ended_at.is_(None), t.c.last_seen < cutoff)
            .order_by(t.c.last_seen)
            .limit(500)
        ).fetchall()
        for row in rows:
            ended = _aware(row.last_seen) + lifetime
            # last_seen too: a request that touched the session since the SELECT
            # keeps it open.
            claimed = conn.execute(
                t.update()
                .where(
                    t.c.sid == row.sid,
                    t.c.ended_at.is_(None),
                    t.c.last_seen == row.last_seen,
                )
                .values(ended_at=ended, end_reason="idle_timeout")
            ).rowcount
            if claimed != 1:
                continue
            records.append(
                _record(
                    "auth",
                    "auth.logout",
                    target_type="ab_user",
                    target_id=row.user_id,
                    target_name=row.username,
                    details={
                        "reason": "idle_timeout",
                        "session": row.sid,
                        "session_started": _iso(_aware(row.started_at)),
                        "last_activity": _iso(_aware(row.last_seen)),
                        "idle_limit_seconds": int(lifetime.total_seconds()),
                        "session_node": row.node,
                    },
                    overrides={"event_time": ended},
                    context={
                        "actor_id": row.user_id,
                        "actor_username": row.username,
                        "ip_address": row.ip_address,
                        "user_agent": row.user_agent,
                        "request": "session idle timeout",
                    },
                )
            )
        if records and _state.db_enabled:
            _insert(conn, records)
    _emit_stdout(records)
    return len(records)


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
    "template_name",
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

    if _sensitive_name(name):
        return True
    return isinstance(getattr(column, "type", None), EncryptedType)


# A connection string inside a parameter value, e.g. odbc_connect=...;PWD=...
_CREDENTIAL_IN_VALUE = re.compile(r"(?i)(pwd|pass(word|wd)?|secret|token)\s*=")


def _sensitive_name(name: str) -> bool:
    name = name.lower()
    return name in SENSITIVE_COLUMNS or any(m in name for m in SENSITIVE_MARKERS)


def _mask_uri(value: Any) -> str:
    """The password and every credential-like query parameter masked."""
    from superset.constants import PASSWORD_MASK
    from superset.databases.utils import make_url_safe

    try:
        url = make_url_safe(str(value))
        query = {
            k: (
                PASSWORD_MASK
                if _sensitive_name(k) or _CREDENTIAL_IN_VALUE.search(str(v))
                else v
            )
            for k, v in url.query.items()
        }
        if not url.password and query == dict(url.query):
            return str(value)
        url = url.set(query=query)
        if url.password:
            url = url.set(password=PASSWORD_MASK)
        return url.render_as_string(hide_password=False)
    except Exception:  # noqa: BLE001
        return REDACTED


def _redact(value: Any) -> Any:
    """Nested dicts/lists with credential-like keys masked (e.g. dbs.extra's
    connect_args)."""
    if isinstance(value, dict):
        return {
            k: (
                REDACTED
                if _sensitive_name(str(k)) and v not in (None, "")
                else _redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _redact_json_text(value: str) -> str:
    """A JSON object/array held in a text column (extra, params, ...), redacted."""
    if not value.lstrip().startswith(("{", "[")):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    redacted = _redact(parsed)
    return value if redacted == parsed else json.dumps(redacted, sort_keys=True)


def _value(name: str, column: Any, value: Any) -> Any:
    """A column value as stored in details: redacted, JSON-safe, cut to length."""
    if value is None:
        return None
    if _is_sensitive(name, column):
        return REDACTED
    if name == "sqlalchemy_uri":
        value = _mask_uri(value)
    elif isinstance(value, (dict, list)):
        value = _redact(value)
    elif isinstance(value, str):
        value = _redact_json_text(value)
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


def _unchanged(old: Any, new: Any) -> bool:
    if old == new:
        return True
    # The same UUID as an object and as text (seen from provision on the lab).
    uuids = isinstance(old, uuid.UUID) or isinstance(new, uuid.UUID)
    return uuids and str(old) == str(new)


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
            if _unchanged(old, new) or (not hist.deleted and new is None):
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


def _permission_holders(conn: Connection, pvm: Any) -> list[tuple[Any, str, str]]:
    """(role id, role name, permission label) for each role holding `pvm`, read with
    Core on the flush's connection (no ORM loads inside a flush)."""
    from flask_appbuilder.security.sqla.models import (
        assoc_permissionview_role as link,
        Permission,
        PermissionView,
        Role,
        ViewMenu,
    )

    role, pv = Role.__table__, PermissionView.__table__
    perm, vm = Permission.__table__, ViewMenu.__table__
    rows = conn.execute(
        sa.select(role.c.id, role.c.name, perm.c.name, vm.c.name)
        .select_from(
            link.join(role, role.c.id == link.c.role_id)
            .join(pv, pv.c.id == link.c.permission_view_id)
            .join(perm, perm.c.id == pv.c.permission_id)
            .join(vm, vm.c.id == pv.c.view_menu_id)
        )
        .where(link.c.permission_view_id == pvm.id)
    ).fetchall()
    return [(r[0], r[1], f"{r[2]} on {r[3]}") for r in rows]


def _permission_removed_records(
    mapper: Any, holders: list[tuple[Any, str, str]]
) -> list[dict[str, Any]]:
    cause = getattr(getattr(mapper, "local_table", None), "name", None) or "unknown"
    return [
        _record(
            "role",
            "role.permission_remove",
            target_type="ab_role",
            target_id=role_id,
            target_name=role_name,
            details={
                "permissions": [label],
                "cause": f"permission deleted by Superset with a {cause} change",
            },
        )
        for role_id, role_name, label in holders
    ]


def _stash_side(conn: Connection, records: list[dict[str, Any]]) -> None:
    if (info := _conn_info(conn)) is not None:
        info.setdefault(_SIDE, []).extend(records)


# ---------------------------------------------------------------------------
# Bulk statements
# ---------------------------------------------------------------------------
def _bulk_values(stmt: Any) -> dict[str, Any]:
    raw = getattr(stmt, "_values", None) or {}
    items = list(raw.items()) or list(getattr(stmt, "_ordered_values", None) or [])
    values = {}
    for key, value in items:
        name = getattr(key, "key", None) or getattr(key, "name", None) or str(key)
        value = getattr(value, "value", value)  # a BindParameter's literal
        values[str(name)] = _value(str(name), None, value)
    return values


def _where_sql(stmt: Any, conn: Connection) -> Optional[str]:
    where = getattr(stmt, "whereclause", None)
    if where is None:
        return None
    try:
        text = str(
            where.compile(dialect=conn.dialect, compile_kwargs={"literal_binds": True})
        )
    except Exception:  # noqa: BLE001 -- a type without a literal form
        text = str(where.compile(dialect=conn.dialect))
    return _cut_long(_redact_json_text(text))


def _affected(conn: Connection, table: Any, stmt: Any) -> list[Any]:
    """Primary keys (whole rows for a link table) the statement is about to change."""
    link = table.name in _SECURITY_LINKS
    cols = list(table.c) if link else list(table.primary_key.columns) or list(table.c)
    query = sa.select(*cols).select_from(table).limit(MAX_BULK_IDS + 1)
    if (where := getattr(stmt, "whereclause", None)) is not None:
        query = query.where(where)
    out = []
    for row in conn.execute(query):
        values = [_plain(v) for v in row]
        out.append(
            dict(zip([c.name for c in cols], values, strict=True))
            if link
            else (values[0] if len(values) == 1 else values)
        )
    return out


def _inserted_rows(stmt: Any, params: Any) -> list[dict[str, Any]]:
    """The rows of an INSERT statement: .values(...) (one row or a list of rows) or
    the executemany parameter list. [] when they cannot be read."""
    multi = getattr(stmt, "_multi_values", None) or ()
    rows: list[Any] = [row for batch in multi for row in batch]
    if not rows and getattr(stmt, "_values", None):
        rows = [stmt._values]  # noqa: SLF001
    if not rows and params:
        rows = list(params) if isinstance(params, (list, tuple)) else [params]
    out = []
    for row in rows:
        if not isinstance(row, dict):
            return []  # positional values: no column names to show
        values = {}
        for key, value in row.items():
            name = str(getattr(key, "key", None) or getattr(key, "name", None) or key)
            # The table's column, for redaction by type (EncryptedType).
            column = stmt.table.c.get(name)
            values[name] = _value(name, column, getattr(value, "value", value))
        out.append(values)
    return out


def _insert_details(stmt: Any, params: Any) -> dict[str, Any]:
    """details of a bulk INSERT, before it runs: the rows (up to MAX_BULK_IDS)."""
    try:
        rows = _inserted_rows(stmt, params)
    except Exception:  # noqa: BLE001 -- record the insert even without its rows
        logger.exception("audit: could not read the rows of a bulk insert")
        rows = []
    details: dict[str, Any] = {}
    if rows:
        details["values"] = rows[:MAX_BULK_IDS]
        if len(rows) > MAX_BULK_IDS:
            details["values_truncated"] = True
    return details


def _filter_details(
    stmt: Any, conn: Connection, table: Any, op: str, values: dict[str, Any]
) -> dict[str, Any]:
    """details of a bulk UPDATE or DELETE, before it runs: filter, values, rows hit."""
    details: dict[str, Any] = {"where": _where_sql(stmt, conn)}
    if values:
        details["values"] = values
    try:
        affected = _affected(conn, table, stmt)
        details["affected"] = affected[:MAX_BULK_IDS]
        if len(affected) > MAX_BULK_IDS:
            details["affected_truncated"] = True
    except Exception:  # noqa: BLE001 -- record the change even without its rows
        logger.exception("audit: could not list the rows of a bulk %s", op)
        details["affected"] = "unavailable"
    return details


def _on_orm_execute(state: ORMExecuteState) -> Any:
    """Session.execute of an INSERT, UPDATE or DELETE (Query.update/delete included):
    these skip the unit of work, so after_flush never sees their rows."""
    if not _state.initialised or not (
        state.is_insert or state.is_update or state.is_delete
    ):
        return None
    stmt = state.statement
    table = getattr(stmt, "table", None)
    name = getattr(table, "name", None)
    if not name or name in EXCLUDED_TABLES:
        return None
    op = "create" if state.is_insert else "update" if state.is_update else "delete"
    values = _bulk_values(stmt) if op == "update" else {}
    noise = NOISE_COLUMNS | (USER_NOISE_COLUMNS if name == "ab_user" else frozenset())
    if op == "update" and values and set(values) <= noise:
        return None  # e.g. FAB's login_count write on a failed login
    conn = state.session.connection()
    if not _is_metadata(conn):
        return None
    if op == "create":
        details = _insert_details(stmt, state.parameters)
    else:
        details = _filter_details(stmt, conn, table, op, values)
    result = state.invoke_statement()
    rowcount = getattr(result, "rowcount", None)
    if rowcount == 0:
        return result
    details["rows"] = rowcount
    category = SECURITY_CATEGORIES.get(name) or (
        _SECURITY_LINKS[name][1] if name in _SECURITY_LINKS else "data"
    )
    record = _record(
        category, f"{category}.bulk_{op}", target_type=name, details=details
    )
    if _state.db_enabled:
        _insert(conn, [record])
    _queue(state.session, conn, [record])
    return result


def _patch_bulk_mappings() -> None:
    """Record Session.bulk_insert_mappings / bulk_update_mappings.

    SQLAlchemy 1.4 runs them outside the unit of work and Session.execute, so neither
    after_flush nor do_orm_execute sees them, and Superset saves dataset columns and
    metrics with them (DatasetDAO.update_columns / update_metrics). Each written row
    becomes a <category>.create or .update event on the same connection, like a flush.
    """
    if getattr(Session.bulk_insert_mappings, "_lauretta_audit", False):
        return
    insert_mappings = Session.bulk_insert_mappings
    update_mappings = Session.bulk_update_mappings

    @functools.wraps(insert_mappings)
    def bulk_insert_mappings(
        self: Session, mapper: Any, mappings: Any, *args: Any, **kwargs: Any
    ) -> Any:
        mappings = list(mappings)
        result = insert_mappings(self, mapper, mappings, *args, **kwargs)
        if target := _mapping_target(self, mapper, mappings):
            _record_mappings(self, *target, mappings, "create", {})
        return result

    @functools.wraps(update_mappings)
    def bulk_update_mappings(
        self: Session, mapper: Any, mappings: Any, *args: Any, **kwargs: Any
    ) -> Any:
        mappings = list(mappings)
        target = _mapping_target(self, mapper, mappings)
        before: dict[tuple[Any, ...], dict[str, Any]] = {}
        try:
            before = _rows_before(*target, mappings) if target else {}
        except Exception:  # noqa: BLE001 -- record the update, without old values
            logger.exception("audit: could not read the rows of a bulk update")
        result = update_mappings(self, mapper, mappings, *args, **kwargs)
        if target:
            _record_mappings(self, *target, mappings, "update", before)
        return result

    for wrapper in (bulk_insert_mappings, bulk_update_mappings):
        wrapper._lauretta_audit = True  # type: ignore[attr-defined]
    Session.bulk_insert_mappings = bulk_insert_mappings
    Session.bulk_update_mappings = bulk_update_mappings


def _mapping_target(
    session: Session, mapper: Any, mappings: list[Any]
) -> Optional[tuple[Any, Connection]]:
    """(mapper, connection) when these rows are audited, else None."""
    if not _state.initialised or not mappings:
        return None
    mapper = sa_inspect(mapper)
    if mapper.local_table.name in EXCLUDED_TABLES:
        return None
    conn = session.connection(bind_arguments={"mapper": mapper})
    return (mapper, conn) if _is_metadata(conn) else None


def _mapping_pk(mapper: Any, mapping: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        mapping.get(mapper.get_property_by_column(col).key)
        for col in mapper.primary_key
    )


def _rows_before(
    mapper: Any, conn: Connection, mappings: list[dict[str, Any]]
) -> dict[tuple[Any, ...], dict[str, Any]]:
    """The current values of the rows an update mapping is about to change, by key."""
    table = mapper.local_table
    attrs = [a for a in mapper.column_attrs if a.columns[0].table is table]
    pk_cols = list(mapper.primary_key)
    query = sa.select(*pk_cols, *[a.columns[0] for a in attrs])
    pks = list({_mapping_pk(mapper, m) for m in mappings})
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for start in range(0, len(pks), 500):
        chunk = pks[start : start + 500]
        where = (
            pk_cols[0].in_([pk[0] for pk in chunk])
            if len(pk_cols) == 1
            else sa.tuple_(*pk_cols).in_(chunk)
        )
        for row in conn.execute(query.where(where)):
            pk = tuple(row[: len(pk_cols)])
            out[pk] = {
                a.key: value
                for a, value in zip(attrs, row[len(pk_cols) :], strict=True)
            }
    return out


def _record_mappings(  # pylint: disable=too-many-arguments
    session: Session,
    mapper: Any,
    conn: Connection,
    mappings: list[dict[str, Any]],
    op: str,
    before: dict[tuple[Any, ...], dict[str, Any]],
) -> None:
    table = mapper.local_table.name
    skip = NOISE_COLUMNS | (USER_NOISE_COLUMNS if table == "ab_user" else frozenset())
    category = SECURITY_CATEGORIES.get(table, "data")
    records = []
    for mapping in mappings:
        pk = _mapping_pk(mapper, mapping)
        old = before.get(pk, {})
        details: dict[str, Any] = {}
        # Column attributes only: an update mapping may be a whole instance __dict__.
        for attr in mapper.column_attrs:
            name = attr.key
            if name in skip or name not in mapping:
                continue
            column, new = attr.columns[0], mapping[name]
            if op == "create":
                if new is not None:
                    details.setdefault("values", {})[name] = _value(name, column, new)
            elif not _unchanged(old.get(name), new):
                details.setdefault("changes", {})[name] = {
                    "old": _value(name, column, old.get(name)),
                    "new": _value(name, column, new),
                }
        if op == "update" and not details:
            continue  # rewritten with the values it already had
        label = next((mapping[a] for a in _LABEL_ATTRS if mapping.get(a)), None)
        target_id: Any = None  # an inserted row's id is not returned
        if any(v is not None for v in pk):
            target_id = pk[0] if len(pk) == 1 else list(pk)
        records.append(
            _record(
                category,
                f"{category}.{op}",
                target_type=table,
                target_id=target_id,
                target_name=label,
                details=details,
            )
        )
    if records and _state.db_enabled:
        _insert(conn, records)  # fails the write if the audit row cannot be stored
    _queue(session, conn, records)


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
    client = _SQL_CLIENTS.pop(str(obj.client_id), None)
    sql = obj.sql or obj.executed_sql or ""
    verdict = _dml_verdict(
        obj.sql,
        obj.executed_sql,
        obj.database.db_engine_spec.engine,
        bool(getattr(obj.database, "allow_dml", True)),
        status,
    )
    if verdict is None:
        return None
    # SQL Lab runs statements under a synthetic request ("GET /"): the client is the
    # one remembered when the query was submitted (same process: synchronous SQL Lab;
    # an async query run by a Celery worker has none) and the actor is the query owner.
    overrides: dict[str, Any] = {"request": f"SQL Lab query {obj.client_id}"}
    if client:
        overrides.update(client)
        overrides["request"] = f"{client['request']} (SQL Lab query {obj.client_id})"
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
            **(
                {"mutation": "unknown: Jinja template, rendered script not stored"}
                if verdict == "template"
                else {}
            ),
        },
        overrides=overrides,
    )


_JINJA_MARKERS = ("{{", "{%", "{#")


def _dml_verdict(
    sql: Optional[str],
    executed_sql: Optional[str],
    engine: str,
    allow_dml: bool,
    status: str,
) -> Optional[str]:
    """Whether a finished SQL Lab query is recorded as data.sql_dml: 'mutation' when
    its SQL writes, 'template' when a Jinja template may have, None for a read.

    Query.sql keeps the template, which often does not parse, and executed_sql holds
    only the last block Superset ran, so a templated write can be invisible in both.
    Superset checks the rendered script against allow_dml, so a templated query that
    succeeded on a database without DML wrote nothing; every other one is recorded
    (a refused attempt fails, so it is recorded too).
    """
    from superset.sql.parse import SQLScript

    def mutates(script: str) -> bool:
        try:
            return SQLScript(script, engine).has_mutation()
        except Exception:  # noqa: BLE001 -- unparsable: see the template rule below
            return False

    if any(mutates(s) for s in dict.fromkeys((sql, executed_sql)) if s):
        return "mutation"
    text = sql or ""
    templated = any(marker in text for marker in _JINJA_MARKERS)
    if templated and (allow_dml or status != "success"):
        return "template"
    return None


def _remember_sql_client(obj: Any) -> None:
    ctx = _context()
    if not ctx.get("ip_address") or not obj.client_id:
        return
    _SQL_CLIENTS[str(obj.client_id)] = {
        k: ctx[k] for k in ("ip_address", "user_agent", "request")
    }
    while len(_SQL_CLIENTS) > _SQL_CLIENTS_MAX:
        _SQL_CLIENTS.popitem(last=False)


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
                if op == "create":
                    _remember_sql_client(obj)
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
    """A bug here must not lose an event or fail the change: degrade to a bare row."""
    records, pairs, events = _describe(session)
    try:
        records += _link_records(pairs, events)
    except Exception:  # noqa: BLE001
        logger.exception("audit: could not describe a role or group link change")
        records.append(
            _record("audit", "audit.incomplete", details={"lost": "link changes"})
        )
    for ev in events.values():
        try:
            record = _object_record(ev)
        except Exception:  # noqa: BLE001
            logger.exception("audit: could not describe a change")
            record = _record(
                SECURITY_CATEGORIES.get(ev["table"], "data"),
                f"{SECURITY_CATEGORIES.get(ev['table'], 'data')}.{ev['op']}",
                target_type=ev["table"],
                details={"error": "details unavailable"},
            )
        if record is not None:
            records.append(record)
    return records


def _is_metadata(conn: Connection) -> bool:
    from flask import has_app_context

    if not has_app_context():
        return False
    from superset.extensions import db

    return str(conn.engine.url) == str(db.engine.url)


def _owns(session: Session, conn: Connection) -> bool:
    """Whether committing `session` commits `conn`'s database transaction. False for a
    Session bound to a Connection already in a transaction: SQLAlchemy 1.4 records
    should_commit=False for it, or, inside a connection savepoint, uses that savepoint
    (its commit only releases it). Reads an internal, so on any surprise: owns."""
    try:
        entry = session.get_transaction()._connections.get(conn)  # noqa: SLF001
        if entry is None:
            return True
        return bool(entry[2]) and not isinstance(entry[1], sa.engine.NestedTransaction)
    except Exception:  # noqa: BLE001
        return True


def _after_flush(session: Session, _flush_context: Any) -> None:
    if not _state.initialised:
        return
    conn = session.connection()
    if not _is_metadata(conn):
        return
    info = _conn_info(conn)
    records: list[dict[str, Any]] = list((info or {}).pop(_SIDE, None) or [])
    if session.new or session.dirty or session.deleted:
        records += _build_records(session)
    if records and _state.db_enabled:
        _insert(
            conn, records
        )  # fails the flush, and the change, if it cannot be written
    _queue(session, conn, records)


def _queue(session: Session, conn: Connection, records: list[dict[str, Any]]) -> None:
    """Hold already-inserted records for stdout until their transaction commits."""
    root = conn.get_transaction()
    if not _owns(session, conn):
        if records:
            conn.info.setdefault(_DEFERRED, []).append((root, records))
        return
    # What sessions joined to this transaction wrote during this flush (tag listeners).
    for txn, deferred in conn.info.pop(_DEFERRED, None) or []:
        if txn is root:
            records = deferred + records
        else:
            _warn_unprinted(deferred)
    if records:
        session.info.setdefault(_PENDING, []).append(
            (session.get_nested_transaction(), records)
        )
        conn.info.setdefault(_OPEN, []).append((_savepoints(conn), records))


def _savepoints(conn: Connection) -> frozenset[str]:
    """Names of the connection savepoints open now (innermost first, then outwards)."""
    names: set[str] = set()
    nested = conn.get_nested_transaction()
    while nested is not None:
        if name := getattr(nested, "_savepoint", None):
            names.add(name)
        nested = getattr(nested, "_previous_nested", None)
    return frozenset(names)


def _after_commit(session: Session) -> None:
    # Also fired when a savepoint is released (the savepoint is still current then);
    # print only when the outermost transaction commits. Only an owning session queues
    # records, so this commit was the database commit.
    if session.get_nested_transaction() is not None:
        return
    if pending := session.info.pop(_PENDING, None):
        _emit_stdout([r for _, records in pending for r in records])


def _rolled_back_to(previous: SessionTransaction) -> Optional[SessionTransaction]:
    """What a rollback of `previous` undid in the database: the innermost savepoint at
    or above it, or None for the whole transaction. A failed flush rolls back its own
    sub-transaction, which reaches the database as the enclosing savepoint or root."""
    txn: Optional[SessionTransaction] = previous
    while txn is not None:
        if txn.nested:
            return txn
        txn = txn.parent
    return None


def _after_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    """Forget records that a rollback undid: everything, or one savepoint's worth."""
    pending = session.info.get(_PENDING)
    if not pending:
        return
    boundary = _rolled_back_to(previous)
    if boundary is None:
        session.info.pop(_PENDING, None)
        return

    def inside(txn: Optional[SessionTransaction]) -> bool:
        while txn is not None:
            if txn is boundary:
                return True
            txn = txn.parent
        return False

    session.info[_PENDING] = [p for p in pending if not inside(p[0])]


def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    # The outermost transaction ended without a commit having printed its records
    # (Session.close() rolls back without after_soft_rollback): they never committed.
    if transaction.parent is None:
        session.info.pop(_PENDING, None)


def _conn_info(conn: Connection) -> Optional[dict[str, Any]]:
    # Connection.info on an invalidated connection would try to reconnect.
    if conn.closed or conn.invalidated:
        return None
    try:
        return conn.info
    except Exception:  # noqa: BLE001 -- cleanup must never break a commit or rollback
        logger.debug("audit: connection info unavailable", exc_info=True)
        return None


def _on_connection_end(conn: Connection) -> None:
    """Fired just before the DBAPI commit. A transaction the server has already aborted
    (an error was caught and the work went on) "commits" as a rollback without an
    exception on PostgreSQL: empty its record lists so nothing is printed."""
    if (info := _conn_info(conn)) is None:
        return
    queued = info.pop(_OPEN, None) or []
    if queued and _commit_is_rollback(conn):
        lost = [r for _, records in queued for r in records]
        for _, records in queued:
            records.clear()
        if lost:
            logger.warning(
                "audit: commit of an aborted transaction: %d event(s) not printed, "
                "their rows were rolled back",
                len(lost),
            )


def _commit_is_rollback(conn: Connection) -> bool:
    try:
        fairy = conn.connection
        dbapi = getattr(fairy, "dbapi_connection", None) or fairy.connection
    except Exception:  # noqa: BLE001
        return False
    # psycopg2: libpq PQTRANS_INERROR (3) = the transaction is aborted.
    status = getattr(getattr(dbapi, "info", None), "transaction_status", None)
    # Other drivers: no reliable signal (sqlite3's in_transaction is also False after
    # an outermost savepoint already committed), so assume the commit is real. Every
    # Lauretta mode keeps its metadata in PostgreSQL.
    return isinstance(status, int) and status == 3


def _on_connection_rollback(conn: Connection) -> None:
    if (info := _conn_info(conn)) is not None:
        info.pop(_OPEN, None)
        info.pop(_DEFERRED, None)
        info.pop(_SIDE, None)


def _on_savepoint_rollback(conn: Connection, name: str, _context: Any) -> None:
    if (info := _conn_info(conn)) is None:
        return
    info.pop(_DEFERRED, None)
    kept = []
    for names, records in info.get(_OPEN) or []:
        if name in names:
            records.clear()  # the session's pending entry holds this same list
        else:
            kept.append((names, records))
    info[_OPEN] = kept


def _on_checkin(_dbapi_conn: Any, record: Any) -> None:
    # Joined-session rows the owner never flushed again: their commit is not observed,
    # so they get no stdout line (the rows themselves are in the table if committed).
    info = getattr(record, "info", None)
    if info is not None:
        info.pop(_OPEN, None)
        info.pop(_SIDE, None)
    if info is not None and (lost := info.pop(_DEFERRED, None)):
        _warn_unprinted([r for _, records in lost for r in records])


def _warn_unprinted(records: list[dict[str, Any]]) -> None:
    if records:
        logger.warning(
            "audit: %d event(s) without a stdout line: their transaction's outcome was "
            "not observed (ids %s; the table has them if they committed)",
            len(records),
            [r.get("id") for r in records],
        )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(old_secrets: Optional[list[str]] = None) -> int:
    """Recompute every row's HMAC. Returns the number of rows that cannot be verified:
    a mismatch, or a key_id none of the known keys has.

    Rows are checked against the current key, the key derived from the current
    SECRET_KEY, and each of `old_secrets` (previous LAURETTA_AUDIT_HMAC_KEY or
    SUPERSET_SECRET_KEY values), so rows written before a key rotation still verify.
    An unknown key_id counts as a failure: key_id is just a column, and whoever can edit
    a row can also set it to a value no key matches.
    """
    from superset.extensions import db

    keys = _known_keys(old_secrets)
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
    _report(ok, bad, unknown, first_bad, missing, gaps)
    record_event(
        "audit",
        "audit.verify",
        outcome="success" if not (bad or unknown) else "failure",
        target_type=TABLE_NAME,
        details={
            "ok": ok,
            "tampered": bad,
            "unknown_key": unknown,
            "missing_ids": missing,
            "os_user": _os_user(),
        },
    )
    return bad + unknown


def _known_keys(old_secrets: Optional[list[str]] = None) -> dict[str, bytes]:
    from flask import current_app

    candidates = [_state.key, _derived_key(str(current_app.config["SECRET_KEY"]))]
    for secret in old_secrets or []:
        candidates += [secret.encode(), _derived_key(secret)]
    return {_key_id(key): key for key in candidates}


def _os_user() -> str:
    for name in ("SUDO_USER", "LOGNAME", "USER"):
        if value := os.environ.get(name, "").strip():
            return value
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 -- no passwd entry for the uid
        return f"uid {os.getuid()}"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
REPORT_COLUMNS = (
    "id",
    "event_time",
    "category",
    "action",
    "outcome",
    "actor_id",
    "actor_username",
    "target_type",
    "target_id",
    "target_name",
    "ip_address",
    "user_agent",
    "request",
    "node",
    "details",
    "seal_ok",
)


def report(  # pylint: disable=too-many-arguments
    out: Any,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    user: Optional[str] = None,
    category: Optional[str] = None,
    action: Optional[str] = None,
    engine: Optional[sa.engine.Engine] = None,
    keys: Optional[dict[str, bytes]] = None,
) -> int:
    """Write the audit rows matching the filters to `out` as CSV, oldest first, each
    with seal_ok (its HMAC checks out). Records an audit.report event: reading the
    audit trail is itself on record. Returns the number of rows."""
    if engine is None:
        from superset.extensions import db

        engine = db.engine
    keys = keys if keys is not None else _known_keys()
    t = audit_table
    query = sa.select(t).order_by(t.c.event_time, t.c.id)
    if since is not None:
        query = query.where(t.c.event_time >= since)
    if until is not None:
        query = query.where(t.c.event_time < until)
    if user:
        query = query.where(t.c.actor_username == user)
    if category:
        query = query.where(t.c.category == category)
    if action:
        query = query.where(t.c.action == action)
    writer = csv.writer(out)
    writer.writerow(REPORT_COLUMNS)
    rows = 0
    with engine.connect() as conn:
        for row in conn.execution_options(stream_results=True).execute(query):
            record = dict(row._mapping)
            key = keys.get(record["key_id"])
            sealed = key is not None and hmac.compare_digest(
                _sign(record, key), record["row_hmac"]
            )
            record["event_time"] = _iso(_aware(record["event_time"]))
            record["seal_ok"] = "yes" if sealed else "NO"
            writer.writerow([record.get(c) for c in REPORT_COLUMNS])
            rows += 1
    filters = {
        "from": since and _iso(since),
        "to": until and _iso(until),
        "user": user,
        "category": category,
        "action": action,
    }
    record_event(
        "audit",
        "audit.report",
        target_type=TABLE_NAME,
        details={
            "filters": {k: v for k, v in filters.items() if v},
            "rows": rows,
            "os_user": _os_user(),
        },
    )
    return rows


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _report_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m lauretta_audit report")
    parser.add_argument("--from", dest="since", type=_day, help="YYYY-MM-DD (UTC)")
    parser.add_argument(
        "--to", dest="until", type=_day, help="YYYY-MM-DD (UTC), inclusive"
    )
    parser.add_argument("--user", help="actor username")
    parser.add_argument("--category", help="auth, user, role, group, data, access, ...")
    parser.add_argument("--action", help="e.g. auth.login")
    parser.add_argument("--out", help="CSV file (default: stdout)")
    args = parser.parse_args(argv)
    if args.until is not None:
        args.until += timedelta(days=1)
    return args


def _report(
    ok: int, bad: int, unknown: int, first_bad: list[int], missing: int, gaps: list[str]
) -> None:
    print(f"audit verify: {ok} ok, {bad} TAMPERED, {unknown} signed with another key")
    if first_bad:
        print(f"audit verify: first tampered ids: {first_bad}")
    if unknown:
        print(
            f"audit verify: FAILED: {unknown} rows name a key that is not known here; "
            f"pass earlier keys in {OLD_KEYS_ENV} (another node's or a rotated key), "
            "or treat those rows as altered"
        )
    if missing:
        # A rolled-back change also leaves a gap: its audit row was rolled back with
        # it. Compare these ranges with the stdout/SIEM copy, which never has them.
        print(
            f"audit verify: {missing} ids absent (rolled back or deleted), "
            f"first ranges: {gaps}"
        )


def _main(argv: list[str]) -> int:
    commands = {"create-table", "verify", "sweep-sessions", "report"}
    if not argv or argv[0] not in commands:
        print(
            "usage: python -m lauretta_audit "
            "{create-table|verify|sweep-sessions|report}",
            file=sys.stderr,
        )
        return 2
    report_args = _report_args(argv[1:]) if argv[0] == "report" else None
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
        if argv[0] == "sweep-sessions":
            print(f"audit: {lauretta_audit.sweep_sessions()} idle session(s) closed")
            return 0
        if report_args is not None:
            return _run_report(lauretta_audit, report_args)
        old = [k for k in os.environ.get(OLD_KEYS_ENV, "").split(",") if k.strip()]
        return 1 if lauretta_audit.verify(old) else 0


def _run_report(module: Any, args: argparse.Namespace) -> int:
    kwargs = {
        "since": args.since,
        "until": args.until,
        "user": args.user,
        "category": args.category,
        "action": args.action,
    }
    if not args.out:
        rows = module.report(sys.stdout, **kwargs)
    else:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as out:
            rows = module.report(out, **kwargs)
    print(f"audit report: {rows} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
