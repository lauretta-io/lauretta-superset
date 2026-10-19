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
Runtime pieces of the HA mode (docker-compose-ha.yml), kept out of superset_config_ha.py
so the one-shot init (docker/docker-init-ha.sh) can import them without loading a second
copy of the config.

  * metadata_uri()            the multi-host libpq URL for the shared metadata database
  * init_sessions(app)        server-side sessions in the metadata database, wired after
                              Superset's own db setup (see the function for why)
  * create_session_table(app) the one-shot, serial creation of that table
  * wait_for_metadata()       the wait in docker/docker-ha-web.sh
  * write_probe               blueprint for the internal readiness write probe

See lauretta/docs/11-ha-mode.md.
"""

import logging
import os
from urllib.parse import quote, urlencode

from flask import Blueprint, abort, jsonify, request

logger = logging.getLogger(__name__)

SESSION_TABLE = "ha_sessions"

# Set by docker/docker-init-ha.sh. The init runs `superset db upgrade` before the session
# table exists, so the runtime check in init_sessions() must not apply there.
INIT_ENV = "SUPERSET_HA_INIT"

# Header the internal nginx server sets on the probe request. The public server overwrites
# it with an empty value on every proxied request, so a client cannot forge it.
PROBE_HEADER = "X-Superset-Ha-Probe"
PROBE_PATH = "/internal/ha/write-probe"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set for SUPERSET_HA_MODE (see docker/.env-ha.example)")
    return value


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def metadata_uri() -> str:
    """postgresql+psycopg2 URL with every libpq parameter in the query string.

    Everything has to be in the URL, not in SQLALCHEMY_ENGINE_OPTIONS["connect_args"]:
    superset/migrations/env.py builds its own engine from the URL alone, so `superset db
    upgrade` would otherwise connect without TLS and to whichever host came first.

    libpq tries the hosts in order and keeps the first one that accepts writes
    (target_session_attrs=read-write); `hostaddr` lets it connect by address while
    verifying the certificate against the name in `host` (no DNS lookup). TCP keepalives
    and tcp_user_timeout make a connection to a host that vanished (power loss, link
    down) fail in seconds instead of after the kernel's retransmission timeout, so
    pool_pre_ping can replace it.
    """
    hosts = _split(_require("DATABASE_HOSTS"))
    addrs = _split(os.environ.get("DATABASE_HOSTADDRS", ""))
    if addrs and len(addrs) != len(hosts):
        raise ValueError(
            f"DATABASE_HOSTADDRS has {len(addrs)} entries, DATABASE_HOSTS {len(hosts)}: "
            "they are matched by position"
        )
    port = _require("DATABASE_PORT")
    query = {
        "host": ",".join(hosts),
        "port": ",".join([port] * len(hosts)),
        "target_session_attrs": "read-write",
        "sslmode": os.environ.get("DATABASE_SSLMODE", "verify-full"),
        "sslrootcert": os.environ.get("DATABASE_SSLROOTCERT", "/app/pgca/ca.pem"),
        "connect_timeout": os.environ.get("DATABASE_CONNECT_TIMEOUT", "3"),
        "keepalives": "1",
        "keepalives_idle": "10",
        "keepalives_interval": "5",
        "keepalives_count": "3",
        "tcp_user_timeout": "15000",
        "application_name": os.environ.get("DATABASE_APPLICATION_NAME", "superset-ha"),
    }
    if addrs:
        query["hostaddr"] = ",".join(addrs)
    user = quote(_require("DATABASE_USER"), safe="")
    password = quote(_require("DATABASE_PASSWORD"), safe="")
    database = quote(_require("DATABASE_DB"), safe="")
    return f"postgresql+psycopg2://{user}:{password}@/{database}?{urlencode(query)}"


def _session_interface_class():
    from flask_session.sqlalchemy import SqlAlchemySessionInterface

    class HaSessionInterface(SqlAlchemySessionInterface):
        """Flask-Session's SQLAlchemy backend, minus one database round trip per
        anonymous request.

        The shared FLASK_APP_MUTATOR marks every session permanent in a before_request
        hook. That marks even an anonymous, empty session as modified, and Flask-Session
        answers "empty and modified" with a DELETE + COMMIT on the metadata database and
        a cookie-deletion header -- for every request without a cookie: each docker
        healthcheck, readiness probe and DNS health check, from every node, to the one
        writable server (measured: 10 anonymous /health requests = 10 DELETEs; with this
        class, 0). A request that sent no session cookie and leaves nothing but
        `_permanent` in the session is skipped.
        """

        def save_session(self, app, session, response):
            # A request that came with a session cookie is saved as usual (refreshed, or
            # deleted after a logout), whatever the session holds.
            if (
                set(session.keys()) <= {"_permanent"}
                and not request.cookies.get(self.get_cookie_name(app))
            ):
                return None
            return super().save_session(app, session, response)

    return HaSessionInterface


def init_sessions(app) -> None:
    """Server-side sessions in the metadata database, shared by every node.

    Superset builds its session backend in configure_session(), which runs *before*
    setup_db() (superset/initialization/__init__.py), so SESSION_SQLALCHEMY=db at config
    time fails with "extension was not registered". superset_config_ha.py therefore keeps
    SESSION_SERVER_SIDE=False for Superset's own init and the HA mutator calls this
    afterwards, when Superset's Flask-SQLAlchemy instance is ready.

    The table is never created here: Flask-Session's constructor would CREATE it on every
    worker of every node at once, and checkfirst does not serialise concurrent CREATEs.
    It is created once, by docker/docker-init-ha.sh. A missing table is a hard error.
    """
    if os.environ.get(INIT_ENV) == "1":
        return
    from sqlalchemy import inspect

    from superset.extensions import db

    if not inspect(db.engine).has_table(SESSION_TABLE):
        raise RuntimeError(
            f"session table {SESSION_TABLE!r} is missing from the metadata database: run "
            "the one-shot init (docker/docker-init-ha.sh) on one node first"
        )
    app.session_interface = _build_interface(app, db)
    logger.info("HA mode: server-side sessions in metadata table %s", SESSION_TABLE)


def _build_interface(app, db):
    from flask_sqlalchemy import BaseQuery

    cfg = app.config
    interface = _session_interface_class()(
        app,
        client=db,
        key_prefix=cfg.get("SESSION_KEY_PREFIX", "session:"),
        use_signer=cfg.get("SESSION_USE_SIGNER", False),
        permanent=cfg.get("SESSION_PERMANENT", True),
        table=SESSION_TABLE,
        cleanup_n_requests=cfg.get("SESSION_CLEANUP_N_REQUESTS"),
    )
    # Flask-Session declares its model on db.Model and reads it through Model.query.
    # Superset's db is Flask-AppBuilder's SQLA, whose Model base lacks the query_class
    # that Flask-SQLAlchemy's query property needs ("type object 'Session' has no
    # attribute 'query_class'" on the first save). Give the session model the stock one.
    interface.sql_session_model.query_class = BaseQuery
    return interface


def create_session_table(app) -> None:
    """One-shot init only: Flask-Session's own DDL (its constructor creates the table)."""
    from superset.extensions import db

    with app.app_context():
        _build_interface(app, db)


def wait_for_metadata(once: bool = False) -> int:
    """docker/docker-ha-web.sh: block until a writable metadata database answers.

    0 = writable and the session table exists; 1 = not writable (once=True only);
    2 = the session table is missing (the one-shot init has not run).
    """
    import time

    from sqlalchemy import create_engine, inspect
    from sqlalchemy.pool import NullPool

    # target_session_attrs=read-write: a connection only succeeds on a writable server.
    engine = create_engine(metadata_uri(), poolclass=NullPool)
    started = time.monotonic()
    last_log = None
    while True:
        try:
            with engine.connect() as conn:
                has_table = inspect(conn).has_table(SESSION_TABLE)
            break
        except Exception as exc:  # noqa: BLE001 -- any failure: not writable (yet)
            if once:
                return 1
            now = time.monotonic()
            if last_log is None or now - last_log >= 30:
                print(f"HA web: no writable metadata database yet ({type(exc).__name__}); "
                      "waiting", flush=True)
                last_log = now
            time.sleep(5)
    if not has_table:
        print(f"HA web: session table {SESSION_TABLE!r} is missing: run the one-shot init "
              "(docker/docker-init-ha.sh) on one node first", flush=True)
        return 2
    if not once:
        print(f"HA web: writable metadata database after {time.monotonic() - started:.0f}s",
              flush=True)
    return 0


# ---------------------------------------------------------------------------
# Internal write probe (readiness)
# ---------------------------------------------------------------------------
# A SELECT or /health cannot tell a writable metadata database from a read-only one.
# This does one disposable write through Superset's own engine and pool, in one
# transaction that is always rolled back: an INSERT into key_value (the table filter
# state and permalinks live in). It fails on a hot standby and on a read-only
# connection, and nothing is ever committed.
#
# Reachable only through the internal nginx server (docker/nginx/superset-ha.conf),
# which nginx publishes on host loopback; the public server answers 404 for /internal/
# and blanks PROBE_HEADER. No login: readiness must not depend on the identity provider.
write_probe = Blueprint("lauretta_ha_probe", __name__)

_PROBE_SQL = """
INSERT INTO key_value (resource, value, uuid, created_on)
VALUES ('lauretta_ha_probe', '\\x00'::bytea, gen_random_uuid(), now())
"""


@write_probe.get(PROBE_PATH)
def ha_write_probe():
    if request.headers.get(PROBE_HEADER) != "internal":
        abort(404)
    from sqlalchemy import text

    from superset.extensions import db

    try:
        with db.engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.execute(text("SET LOCAL statement_timeout = '3s'"))
                conn.execute(text("SET LOCAL lock_timeout = '1s'"))
                server = conn.execute(
                    text("SELECT inet_server_addr()::text, pg_is_in_recovery()")
                ).one()
                conn.execute(text(_PROBE_SQL))
            finally:
                tx.rollback()
    except Exception as exc:  # noqa: BLE001 -- any failure means "not writable"
        logger.warning("HA write probe failed: %s", exc)
        return jsonify(writable=False, error=type(exc).__name__), 503
    return jsonify(writable=True, server=server[0], in_recovery=server[1])
