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
HA (active-active) overrides: several Superset web nodes, one shared metadata database.

Loaded through SUPERSET_CONFIG_PATH, which docker-compose-ha.yml sets. Superset then loads
this file *instead of* the `superset_config` module (superset/config.py), so it imports
that module itself. With SUPERSET_SECURE_MODE=true (also forced by the compose file) that
import already layers superset_config_secure on top of the shared config, so the order is
shared -> secure -> this file. Everything secure mode hardens stays as it is; only the
settings below change. See lauretta/docs/11-ha-mode.md.

Changes against secure mode:
  * metadata DB: a multi-host libpq URL (lauretta_ha.metadata_uri), so every node finds
    the one writable server after a failover without a restart
  * sessions: server-side in that database instead of node-local Redis, so a user can
    move between nodes; wired after Superset's db setup by FLASK_APP_MUTATOR, which
    *chains* the shared mutator (Lauretta routes, floor-map hooks) rather than replacing it
  * filter state / explore form data: the metastore (key_value), pinned explicitly
  * no Celery: alerts, reports, thumbnails and async queries are off
  * an internal write probe for readiness (lauretta_ha.write_probe, via BLUEPRINTS)
"""

import logging
import os
import sys
from datetime import timedelta

import lauretta_ha
import superset_config as _shared
from superset_config import *  # noqa: F403

from superset.key_value.types import JsonKeyValueCodec

logger = logging.getLogger()

# HA mode builds on secure mode; it never runs with development defaults.
if "superset_config_secure" not in sys.modules:
    raise ValueError(
        "SUPERSET_HA_MODE needs secure mode underneath: set SUPERSET_SECURE_MODE=true and "
        "put /app/docker/pythonpath_secure on PYTHONPATH (docker-compose-ha.yml does both)"
    )
# superset/config.py applies this one last, after every config file: it would silently
# point a node at a single host again.
if os.environ.get("SUPERSET__SQLALCHEMY_DATABASE_URI"):
    raise ValueError("SUPERSET__SQLALCHEMY_DATABASE_URI must not be set in HA mode")

# ---------------------------------------------------------------------------
# Metadata database
# ---------------------------------------------------------------------------
SQLALCHEMY_DATABASE_URI = lauretta_ha.metadata_uri()
# pool_pre_ping replaces a pooled connection that died with the old primary (demote,
# restart, power loss) on its next checkout; pool_recycle bounds how long any connection
# is kept, so none outlives a role change by much.
#
# Pool per gunicorn worker. Every node's workers connect to the same server, so the
# server must allow nodes x SERVER_WORKER_AMOUNT x (pool_size + max_overflow) plus a few
# (init, probes): two nodes x 4 workers x (5 + 5) = 80 against PostgreSQL's default
# max_connections of 100. Raise max_connections before raising these.
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": int(os.environ.get("DATABASE_POOL_RECYCLE", "120")),
    "pool_size": int(os.environ.get("DATABASE_POOL_SIZE", "5")),
    "max_overflow": int(os.environ.get("DATABASE_MAX_OVERFLOW", "5")),
}

# ---------------------------------------------------------------------------
# Sessions: server-side, in the metadata database (lauretta_ha.init_sessions)
# ---------------------------------------------------------------------------
# False on purpose: Superset would otherwise build the backend before its db exists.
# The HA mutator below installs the SQLAlchemy backend instead. Cookies, lifetime and
# the signer stay as secure mode sets them (30-minute rolling idle timeout).
SESSION_SERVER_SIDE = False
SESSION_TYPE = "sqlalchemy"
SESSION_REDIS = None
SESSION_KEY_PREFIX = "session:"
# Expired rows are deleted on read; this also sweeps them on about 1 request in 1000.
SESSION_CLEANUP_N_REQUESTS = 1000
PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

_lauretta_mutator = getattr(_shared, "FLASK_APP_MUTATOR", None)


def FLASK_APP_MUTATOR(app):  # noqa: N802 -- Superset config name
    # Shared mutator first: session.permanent, the /api/v1/lauretta/* routes and the
    # floor-map hooks must register exactly as in every other mode.
    if _lauretta_mutator is not None:
        _lauretta_mutator(app)
    lauretta_ha.init_sessions(app)


# ---------------------------------------------------------------------------
# State that must be shared between nodes lives in the metastore
# ---------------------------------------------------------------------------
# Superset's defaults today, pinned so a change in the shared config cannot move them to
# the node-local Redis.
FILTER_STATE_CACHE_CONFIG = {
    "CACHE_TYPE": "SupersetMetastoreCache",
    "CACHE_DEFAULT_TIMEOUT": int(timedelta(days=90).total_seconds()),
    "REFRESH_TIMEOUT_ON_RETRIEVAL": True,
    "CODEC": JsonKeyValueCodec(),
}
EXPLORE_FORM_DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "SupersetMetastoreCache",
    "CACHE_DEFAULT_TIMEOUT": int(timedelta(days=7).total_seconds()),
    "REFRESH_TIMEOUT_ON_RETRIEVAL": True,
    "CODEC": JsonKeyValueCodec(),
}
# CACHE_CONFIG / DATA_CACHE_CONFIG stay on the node-local Redis (shared config): losing
# them only costs a re-query. So do the rate-limit counters (secure config): each node
# counts its own clients.

# ---------------------------------------------------------------------------
# No Celery in HA mode
# ---------------------------------------------------------------------------
FEATURE_FLAGS = {
    **FEATURE_FLAGS,  # noqa: F405
    "ALERT_REPORTS": False,
    "THUMBNAILS": False,
    "GLOBAL_ASYNC_QUERIES": False,
}

# ---------------------------------------------------------------------------
# Internal write probe for readiness
# ---------------------------------------------------------------------------
BLUEPRINTS = [*getattr(_shared, "BLUEPRINTS", []), lauretta_ha.write_probe]

logger.info("Loaded HA configuration at [%s]", __file__)
