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
Hardened configuration overrides for the secure deployment.

Loaded by docker/pythonpath_dev/superset_config.py when SUPERSET_SECURE_MODE=true,
which docker/.env-secure sets. It is imported *last* — after superset_config_docker —
so a stale local override file cannot silently weaken these settings.

Assumes TLS is terminated by an nginx reverse proxy on the host that forwards to
127.0.0.1:8088 with X-Forwarded-Proto set. See docker/nginx/superset-secure.conf.

Do NOT define FLASK_APP_MUTATOR here: it would shadow the one in the shared config
and unregister the /api/v1/lauretta/* routes.
"""

import os
from datetime import timedelta
from urllib.parse import quote, urlparse

from redis import Redis

# ---------------------------------------------------------------------------
# Fail fast on placeholder secrets
# ---------------------------------------------------------------------------
# A secure stack that silently boots with the dev SECRET_KEY is worse than one that
# refuses to start, so these are hard errors rather than warnings.

_DEV_SECRET_KEYS = {
    "TEST_NON_DEV_SECRET",
    "CHANGE_ME_TO_A_COMPLEX_RANDOM_SECRET",
    "thisISaSECRET_1234",
}


def _require_secret(name: str, min_length: int = 32) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(
            f"{name} must be set in docker/.env-secure when SUPERSET_SECURE_MODE "
            f"is enabled. Run ./scripts/generate-secure-secrets.sh to generate one."
        )
    if value in _DEV_SECRET_KEYS:
        raise ValueError(
            f"{name} is still set to a well-known development placeholder. "
            f"Generate a unique value before deploying."
        )
    if len(value) < min_length:
        raise ValueError(
            f"{name} must be at least {min_length} characters "
            f"(got {len(value)}) when SUPERSET_SECURE_MODE is enabled."
        )
    return value


SECRET_KEY = _require_secret("SUPERSET_SECRET_KEY", min_length=42)
GUEST_TOKEN_JWT_SECRET = _require_secret("GUEST_TOKEN_SECRET")

# Public URL is needed for report links and to sanity-check the TLS assumption.
SUPERSET_PUBLIC_URL = os.environ.get("SUPERSET_PUBLIC_URL", "").strip().rstrip("/")
if not SUPERSET_PUBLIC_URL:
    raise ValueError(
        "SUPERSET_PUBLIC_URL must be set in docker/.env-secure, e.g. "
        "https://superset.example.com"
    )
if urlparse(SUPERSET_PUBLIC_URL).scheme != "https":
    raise ValueError(
        f"SUPERSET_PUBLIC_URL must use https, got {SUPERSET_PUBLIC_URL!r}. "
        "The secure stack assumes TLS is terminated by the reverse proxy."
    )

# ---------------------------------------------------------------------------
# Reverse proxy awareness
# ---------------------------------------------------------------------------
# Without this, request.is_secure is False behind nginx. Consequences: Flask-Talisman
# emits no Strict-Transport-Security header, force_https redirects loop, and Superset
# builds http:// absolute URLs. This is the single highest-leverage setting here.
ENABLE_PROXY_FIX = True
PROXY_FIX_CONFIG = {"x_for": 1, "x_proto": 1, "x_host": 1, "x_port": 1, "x_prefix": 1}

# ---------------------------------------------------------------------------
# Session and cookie handling
# ---------------------------------------------------------------------------
SESSION_COOKIE_SECURE = True  # upstream default is False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Rolling 30-minute idle timeout. Sessions are marked permanent by the
# FLASK_APP_MUTATOR in the shared config, which is what makes this an idle rather
# than an absolute timeout.
PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
SESSION_REFRESH_EACH_REQUEST = True

# Server-side sessions: keeps the session cookie small and, more importantly, makes
# sessions revocable — deleting the Redis key logs the user out immediately, which a
# signed client-side cookie cannot do.
_REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
_REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "") or None

SESSION_SERVER_SIDE = True
SESSION_TYPE = "redis"
SESSION_REDIS = Redis(
    host=_REDIS_HOST,
    port=_REDIS_PORT,
    password=_REDIS_PASSWORD,
    db=int(os.environ.get("REDIS_SESSION_DB", "2")),
)

# ---------------------------------------------------------------------------
# Security headers / CSP (Flask-Talisman)
# ---------------------------------------------------------------------------
# Note on style-src 'unsafe-inline': Superset's frontend uses Emotion CSS-in-JS,
# which injects inline <style> blocks. Removing it breaks the UI. Upstream ships the
# same allowance. script-src stays nonce-based with 'strict-dynamic'.
TALISMAN_ENABLED = True
TALISMAN_CONFIG = {
    "content_security_policy": {
        "base-uri": ["'self'"],
        "default-src": ["'self'"],
        "img-src": ["'self'", "blob:", "data:"],
        "worker-src": ["'self'", "blob:"],
        "connect-src": ["'self'"],
        "object-src": "'none'",
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "script-src": ["'self'", "'strict-dynamic'"],
    },
    "content_security_policy_nonce_in": ["script-src"],
    "force_https": True,
    "force_https_permanent": True,
    "session_cookie_secure": True,
    "session_cookie_http_only": True,
    "strict_transport_security": True,
    "strict_transport_security_max_age": 31536000,
    "strict_transport_security_include_subdomains": True,
    "frame_options": "DENY",
    "referrer_policy": "strict-origin-when-cross-origin",
}
CONTENT_SECURITY_POLICY_WARNING = True

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
# Upstream gates RATELIMIT_ENABLED on SUPERSET_ENV == "production", which is easy to
# lose accidentally. Set it explicitly. Redis storage is required: with the default
# in-memory backend each gunicorn worker keeps its own counters, so the effective
# limit is N x the configured value, and flask-limiter logs a warning.
RATELIMIT_ENABLED = True
_ratelimit_auth = f":{quote(_REDIS_PASSWORD, safe='')}@" if _REDIS_PASSWORD else ""
RATELIMIT_STORAGE_URI = (
    f"redis://{_ratelimit_auth}{_REDIS_HOST}:{_REDIS_PORT}/"
    f"{os.environ.get('REDIS_RATELIMIT_DB', '3')}"
)
RATELIMIT_APPLICATION = "50 per second"
AUTH_RATE_LIMITED = True
AUTH_RATE_LIMIT = "5 per second"

# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
WTF_CSRF_ENABLED = True
# Upstream default is one week; an 8-hour token lifetime bounds replay while still
# outlasting a normal working session.
WTF_CSRF_TIME_LIMIT = int(timedelta(hours=8).total_seconds())

# ---------------------------------------------------------------------------
# Information disclosure
# ---------------------------------------------------------------------------
SHOW_STACKTRACE = False
# Note: the scarf.sh telemetry pixel is disabled at *build* time, not here — it is
# baked into the frontend bundle by webpack (superset-frontend/webpack.config.js).
# docker-compose-secure.yml passes SCARF_ANALYTICS=false as a build arg. That is also
# why this CSP omits the scarf.sh img-src entries upstream ships.

# ---------------------------------------------------------------------------
# Authentication / authorisation
# ---------------------------------------------------------------------------
AUTH_USER_REGISTRATION = False
# Keeps the Public role empty, so anonymous users inherit no permissions.
PUBLIC_ROLE_LIKE = None
# Deliberately NOT setting AUTH_ROLE_PUBLIC = None: Flask-AppBuilder creates the
# public role by name at startup, so a None value makes it attempt a nameless
# INSERT into ab_role and log a NotNullViolation on every boot. PUBLIC_ROLE_LIKE
# above is the setting that actually removes anonymous access.

# ---------------------------------------------------------------------------
# Database connection safety
# ---------------------------------------------------------------------------
PREVENT_UNSAFE_DB_CONNECTIONS = True
PREVENT_UNSAFE_DEFAULT_URLS_ON_DATASET = True

# ---------------------------------------------------------------------------
# Request size limits
# ---------------------------------------------------------------------------
# Backstop for the floor-map upload endpoint. nginx enforces its own
# client_max_body_size; this one applies even if the app is reached directly.
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# ---------------------------------------------------------------------------
# Alerts and reports
# ---------------------------------------------------------------------------
# Address the headless browser fetches, and the user-facing link in the email.
#
# Both are the public URL here. An internal "http://superset:8088" cannot work in
# this mode: TALISMAN_CONFIG["force_https"] answers every plain-HTTP request with a
# 301 to https://superset:8088, where nothing terminates TLS, so the browser hangs
# until SCREENSHOT_PLAYWRIGHT_DEFAULT_TIMEOUT and the report fails with
# ReportScheduleScreenshotFailedError. Superset builds the Playwright context in
# superset/utils/webdriver.py with no hook for extra headers, so the alternative --
# sending X-Forwarded-Proto: https on the internal request -- is not reachable from
# config. Screenshots therefore go out through nginx and back; the worker needs
# public DNS and egress for SUPERSET_PUBLIC_URL.
WEBDRIVER_BASEURL = os.environ.get("WEBDRIVER_BASEURL", SUPERSET_PUBLIC_URL)
WEBDRIVER_BASEURL_USER_FRIENDLY = SUPERSET_PUBLIC_URL
