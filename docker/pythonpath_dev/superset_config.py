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
# This file is included in the final Docker image and SHOULD be overridden when
# deploying the image to prod. Settings configured here are intended for use in local
# development environments. Also note that superset_config_docker.py is imported
# as a final step as a means to override "defaults" configured here
#
import json
import logging
import os
import sys
import uuid
import re
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from datetime import timedelta

from celery.schedules import crontab
from flask import abort, send_file, jsonify, request, session
from flask_caching.backends.filesystemcache import FileSystemCache
from flask_login import current_user

logger = logging.getLogger()


def _require_authenticated_user(view):
    """Reject anonymous callers with a JSON 401.

    Deliberately not flask_login's @login_required: that hands off to
    login_manager.unauthorized(), which builds a redirect with url_for("login").
    Flask-AppBuilder registers the login endpoint as "AuthDBView.login", so the
    lookup raises BuildError and the route returns 500 instead of rejecting
    cleanly. These are JSON endpoints, so 401 is the right answer anyway.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return jsonify(error="Authentication required"), 401
        return view(*args, **kwargs)

    return wrapper


DATABASE_DIALECT = os.getenv("DATABASE_DIALECT")
DATABASE_USER = os.getenv("DATABASE_USER")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_PORT = os.getenv("DATABASE_PORT")
DATABASE_DB = os.getenv("DATABASE_DB")

EXAMPLES_USER = os.getenv("EXAMPLES_USER")
EXAMPLES_PASSWORD = os.getenv("EXAMPLES_PASSWORD")
EXAMPLES_HOST = os.getenv("EXAMPLES_HOST")
EXAMPLES_PORT = os.getenv("EXAMPLES_PORT")
EXAMPLES_DB = os.getenv("EXAMPLES_DB")

# The SQLAlchemy connection string.
SQLALCHEMY_DATABASE_URI = (
    f"{DATABASE_DIALECT}://"
    f"{DATABASE_USER}:{DATABASE_PASSWORD}@"
    f"{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_DB}"
)

SQLALCHEMY_EXAMPLES_URI = (
    f"{DATABASE_DIALECT}://"
    f"{EXAMPLES_USER}:{EXAMPLES_PASSWORD}@"
    f"{EXAMPLES_HOST}:{EXAMPLES_PORT}/{EXAMPLES_DB}"
)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_CELERY_DB = os.getenv("REDIS_CELERY_DB", "0")
REDIS_RESULTS_DB = os.getenv("REDIS_RESULTS_DB", "1")
# Empty in dev/nondev mode, where Redis has no `requirepass`. The secure stack sets
# it, and every Redis URL/connection below has to carry it or caching, Celery and
# server-side sessions all fail to authenticate.
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")


def redis_url(db: str | int) -> str:
    """Redis URL for `db`, with credentials only when a password is configured."""
    auth = f":{quote(REDIS_PASSWORD, safe='')}@" if REDIS_PASSWORD else ""
    return f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{db}"


RESULTS_BACKEND = FileSystemCache("/app/superset_home/sqllab")

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": REDIS_HOST,
    "CACHE_REDIS_PORT": REDIS_PORT,
    "CACHE_REDIS_DB": REDIS_RESULTS_DB,
}
if REDIS_PASSWORD:
    CACHE_CONFIG["CACHE_REDIS_PASSWORD"] = REDIS_PASSWORD
DATA_CACHE_CONFIG = CACHE_CONFIG


class CeleryConfig:
    broker_url = redis_url(REDIS_CELERY_DB)
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
    )
    result_backend = redis_url(REDIS_RESULTS_DB)
    worker_prefetch_multiplier = 1
    task_acks_late = False
    beat_schedule = {
        "reports.scheduler": {
            "task": "reports.scheduler",
            "schedule": crontab(minute="*", hour="*"),
        },
        "reports.prune_log": {
            "task": "reports.prune_log",
            "schedule": crontab(minute=10, hour=0),
        },
        "lauretta.cleanup_temp_images": {
            "task": "lauretta.cleanup_temp_images",
            "schedule": crontab(hour="0", minute="0")
        }
    }


CELERY_CONFIG = CeleryConfig


# Is Playwright available in this image?
#
# Dockerfile installs it, and the chromium it drives, only when INCLUDE_CHROMIUM or
# INCLUDE_FIREFOX is true. That is the default, but docker-compose.yml overrides it
# to false for dev so a development build does not pull ~280 MB of browser it will
# probably never use. So the answer differs per mode, and is a property of the image
# rather than of the deployment.
try:
    import playwright  # noqa: F401

    _HAS_PLAYWRIGHT = True
except ModuleNotFoundError:
    _HAS_PLAYWRIGHT = False

FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "ALERT_REPORT_TABS": True,
    "ALLOW_ADHOC_SUBQUERY": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    # Take screenshots with Playwright rather than Selenium, wherever Playwright is
    # in the image.
    #
    # No image ships chromedriver, so the Selenium path depends on Selenium Manager
    # fetching one at runtime into $HOME/.cache/selenium. That works in modes 1 and
    # 2, which run as root, and fails with EACCES in secure mode, where the process
    # is uid 1000 and .cache is root-owned. Playwright only reads the chromium
    # already in the image, so it needs neither a download nor write access.
    #
    # Keyed on availability rather than on mode. superset/utils/webdriver.py imports
    # playwright at module import time when this is on, and playwright is an optional
    # extra (pyproject.toml "playwright = [...]"), so turning it on unconditionally
    # makes any image built without it die at init with ModuleNotFoundError. Keying
    # it this way means nondev and secure both get Playwright — so report behaviour
    # in nondev actually predicts secure — while a dev image built without browsers
    # falls back to Selenium instead of failing. `INCLUDE_CHROMIUM=true docker
    # compose up --build` gives a dev image that opts into the same engine.
    #
    # Secure mode does not tolerate the fallback: superset_config_secure.py refuses
    # to start if Playwright is missing, because Selenium cannot work as uid 1000.
    "PLAYWRIGHT_REPORTS_AND_THUMBNAILS": _HAS_PLAYWRIGHT,
}
ALERT_REPORTS_NOTIFICATION_DRY_RUN = False
SCREENSHOT_LOCATE_WAIT = 100
SCREENSHOT_LOAD_WAIT = 600

# Slack configuration
SLACK_API_TOKEN = ""

# Email configuration
def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_STARTTLS = _env_bool("SMTP_STARTTLS", True)
SMTP_SSL_SERVER_AUTH = _env_bool("SMTP_SSL_SERVER_AUTH", True)
SMTP_SSL = _env_bool("SMTP_SSL", False)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_MAIL_FROM = os.getenv("SMTP_MAIL_FROM", SMTP_USER)
EMAIL_REPORTS_SUBJECT_PREFIX = os.getenv(
    "EMAIL_REPORTS_SUBJECT_PREFIX", "[Superset] "
)

# WebDriver configuration
# If you use Firefox, you can stick with default values
# If you use Chrome, then add the following WEBDRIVER_TYPE and WEBDRIVER_OPTION_ARGS
WEBDRIVER_TYPE = "chrome"
WEBDRIVER_OPTION_ARGS = [
    "--force-device-scale-factor=2.0",
    "--high-dpi-support=2.0",
    "--headless",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-extensions",
]

# This is for internal use, you can keep http
WEBDRIVER_BASEURL = "http://superset:8088" # When running using docker compose use "http://superset_app:8088'
# This is the link sent to the recipient. Change to your domain, e.g. https://superset.mydomain.com
WEBDRIVER_BASEURL_USER_FRIENDLY = "http://localhost:8088"
SQLLAB_CTAS_NO_LIMIT = True

# Log users out after 30 minutes of inactivity.
# SESSION_REFRESH_EACH_REQUEST re-stamps the cookie expiry on every request,
# turning PERMANENT_SESSION_LIFETIME into a rolling (idle) timeout instead of an
# absolute one. Sessions are marked permanent in FLASK_APP_MUTATOR below.
PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
SESSION_REFRESH_EACH_REQUEST = True

log_level_text = os.getenv("SUPERSET_LOG_LEVEL", "INFO")
LOG_LEVEL = getattr(logging, log_level_text.upper(), logging.INFO)

LAURETTA_IMAGES_DIR = Path("/app/lauretta/images")
LAURETTA_CUSTOM_IMAGES_DIR = Path("/app/lauretta/images/customs")
LAURETTA_TEMP_IMAGES_DIR = Path("/app/lauretta/images/tmp")
# Accepted upload extensions, each mapped to a test of its magic bytes. One dict
# rather than a set plus a lookup table: membership is what makes an extension
# allowed and the value is what confirms the bytes match, so adding an extension
# without a signature for it is not expressible.
#
# The check confirms an upload really is the type its filename claims, so the
# Content-Type that send_file derives from the extension can't disagree with the
# bytes on disk. Deliberately dependency-free: Pillow is only in
# requirements/development.txt, so it is absent from the lean-based production image.
ALLOWED_FLOOR_IMAGE_EXTENSIONS = {
    ".jpg": lambda head: head.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda head: head.startswith(b"\xff\xd8\xff"),
    ".png": lambda head: head.startswith(b"\x89PNG\r\n\x1a\n"),
    ".gif": lambda head: head.startswith((b"GIF87a", b"GIF89a")),
    ".webp": lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
}

"""
By default, the app will use the standard Superset banner. 
To use a custom banner image and app name, follow these steps:

1. add custom banner image to /lauretta/branding directory
2. uncomment APP_ICON below and set the file name as "/static/assets/branding/{your_customer_banner.png}"
3. APP_NAME can also be set to change the app name shown in the browser tab

/lauretta/branding is mounted to /app/superset/static/assets/branding in the Docker image [see superset-volumes configured in docker-compose.yml & docker-compose-non-dev.yml]
and referenced within the app via "/static/assets/branding/{image_name}"
"""
# APP_ICON = "/static/assets/branding/lauretta-banner.png"
# APP_NAME = "Lauretta Superset"

def _resolve_floor_image(floor_ref: str) -> Path | None:
    if not floor_ref:
        return None

    cleaned_ref = floor_ref.strip().lstrip("/")
    if cleaned_ref.startswith("locked:"):
        cleaned_ref = cleaned_ref[len("locked:") :].strip().lstrip("/")
    if cleaned_ref.startswith("api/v1/lauretta/images/"):
        cleaned_ref = cleaned_ref.split("api/v1/lauretta/images/", 1)[1]
    cleaned_ref = cleaned_ref.split("?", 1)[0].split("#", 1)[0]

    if cleaned_ref.startswith("customs/"):
        custom_ref = cleaned_ref.split("/", 1)[1] if "/" in cleaned_ref else ""
        custom_name = Path(custom_ref).name
        if custom_name and LAURETTA_CUSTOM_IMAGES_DIR.exists():
            candidate_custom = (LAURETTA_CUSTOM_IMAGES_DIR / custom_name).resolve()
            if (
                candidate_custom.is_file()
                and candidate_custom.parent == LAURETTA_CUSTOM_IMAGES_DIR.resolve()
                and candidate_custom.suffix.lower() in ALLOWED_FLOOR_IMAGE_EXTENSIONS
            ):
                return candidate_custom

    if cleaned_ref.startswith("tmp/"):
        temp_ref = cleaned_ref.split("/", 1)[1] if "/" in cleaned_ref else ""
        temp_name = Path(temp_ref).name
        if temp_name and LAURETTA_TEMP_IMAGES_DIR.exists():
            candidate_temp = (LAURETTA_TEMP_IMAGES_DIR / temp_name).resolve()
            if (
                candidate_temp.is_file()
                and candidate_temp.parent == LAURETTA_TEMP_IMAGES_DIR.resolve()
                and candidate_temp.suffix.lower() in ALLOWED_FLOOR_IMAGE_EXTENSIONS
            ):
                return candidate_temp

    if not LAURETTA_IMAGES_DIR.exists():
        return None

    candidate_by_name = (LAURETTA_IMAGES_DIR / cleaned_ref).resolve()
    if (
        candidate_by_name.is_file()
        and candidate_by_name.parent == LAURETTA_IMAGES_DIR.resolve()
        and candidate_by_name.suffix.lower() in ALLOWED_FLOOR_IMAGE_EXTENSIONS
    ):
        return candidate_by_name

    target_code = cleaned_ref.lower()
    if target_code == "default":
        target_code = ""

    matched: list[Path] = []
    for image_path in sorted(LAURETTA_IMAGES_DIR.iterdir()):
        if not image_path.is_file():
            continue
        if image_path.suffix.lower() not in ALLOWED_FLOOR_IMAGE_EXTENSIONS:
            continue
        if not target_code:
            matched.append(image_path)
            continue
        stem_parts = image_path.stem.split("_")
        floor_code = stem_parts[-1] if len(stem_parts) > 1 else image_path.stem
        if floor_code.lower() == target_code:
            matched.append(image_path)

    return matched[0] if matched else None


def _cleanup_temp_images():
    """Remove all files from temp folder."""

    if not LAURETTA_TEMP_IMAGES_DIR.exists():
        return 0

    deleted_count = 0

    for image_path in LAURETTA_TEMP_IMAGES_DIR.iterdir():
        if not image_path.is_file():
            continue
        try:
            image_path.unlink()
            deleted_count += 1
        except OSError:
            pass

    return deleted_count


def FLASK_APP_MUTATOR(app):
   # ── Mark every session permanent so PERMANENT_SESSION_LIFETIME applies ──
   @app.before_request
   def _make_session_permanent():
       session.permanent = True

   # ── Register celery task for cleaning temp images ──
   from superset.extensions import celery_app

   @celery_app.task(name="lauretta.cleanup_temp_images")
   def celery_cleanup_temp_images():
       """Celery task to clean up all files from temp floor-map folder."""
       deleted = _cleanup_temp_images()
       return f"Deleted {deleted} temp floor-map images"

   # ── Auto-delete uploaded floor-map images when a chart is deleted ──
   def _on_slice_delete(mapper, connection, target):
       """Called by SQLAlchemy just before a Slice row is deleted."""
       try:
           if target.viz_type != "ext-floor-map":
               return
           params = json.loads(target.params or "{}")
           floor_image: str = params.get("floor_image", "") or ""
           prefix = "/api/v1/lauretta/images/customs/"
           normalized = floor_image.strip()
           if not normalized.startswith(prefix):
               return
           file_name = normalized[len(prefix):].split("?")[0].split("#")[0]
           if "/" in file_name or "\\" in file_name or not file_name:
               return
           candidate = (LAURETTA_CUSTOM_IMAGES_DIR / file_name).resolve()
           if candidate.parent != LAURETTA_CUSTOM_IMAGES_DIR.resolve():
               return
           if candidate.is_file():
               candidate.unlink()
               app.logger.info("Auto-deleted floor-map image: %s", candidate)
       except Exception as exc:  # noqa: BLE001
           app.logger.warning("Could not auto-delete floor-map image: %s", exc)

   # ── Handle floor-map image move on chart create ──
   def _on_slice_create(mapper, connection, target):
       """Called by SQLAlchemy just before a Slice row is inserted.
       - Moves temp images to customs folder
       """
       import shutil

       try:
           if target.viz_type != "ext-floor-map":
               return

           params = json.loads(target.params or "{}")
           floor_image: str = (params.get("floor_image", "") or "").strip()

           temp_prefix = "/api/v1/lauretta/images/tmp/"
           customs_prefix = "/api/v1/lauretta/images/customs/"

           # If image is from temp/, move to customs/
           if floor_image.startswith(temp_prefix):
               temp_file_name = floor_image[len(temp_prefix):].split("?")[0].split("#")[0]
               if temp_file_name and "/" not in temp_file_name and "\\" not in temp_file_name:
                   temp_path = (LAURETTA_TEMP_IMAGES_DIR / temp_file_name).resolve()
                   if (
                       temp_path.is_file()
                       and temp_path.parent == LAURETTA_TEMP_IMAGES_DIR.resolve()
                   ):
                       LAURETTA_CUSTOM_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                       customs_path = (LAURETTA_CUSTOM_IMAGES_DIR / temp_file_name).resolve()
                       if customs_path.parent == LAURETTA_CUSTOM_IMAGES_DIR.resolve():
                           shutil.move(str(temp_path), str(customs_path))
                           # Update params with new customs path
                           params["floor_image"] = f"{customs_prefix}{temp_file_name}"
                           target.params = json.dumps(params)
                           app.logger.info("Moved floor-map image from temp to customs: %s", temp_file_name)

       except Exception as exc:  # noqa: BLE001
           app.logger.warning("Could not handle floor-map image creation: %s", exc)

   # ── Handle floor-map image move on chart update ──
   def _on_slice_update(mapper, connection, target):
       """Called by SQLAlchemy just before a Slice row is updated.
       - Moves temp images to customs folder
       - Deletes old custom images when replaced
       """
       import shutil
       import sqlalchemy as sqla

       try:
           if target.viz_type != "ext-floor-map":
               return

           new_params = json.loads(target.params or "{}")
           new_floor_image: str = (new_params.get("floor_image", "") or "").strip()

           # Get old floor_image from history
           old_floor_image = ""
           history = sqla.orm.attributes.get_history(target, "params")
           if history.deleted:
               old_params_str = history.deleted[0]
               if old_params_str:
                   old_params = json.loads(old_params_str)
                   old_floor_image = (old_params.get("floor_image", "") or "").strip()

           temp_prefix = "/api/v1/lauretta/images/tmp/"
           customs_prefix = "/api/v1/lauretta/images/customs/"

           # If new image is from temp/, move to customs/
           if new_floor_image.startswith(temp_prefix):
               temp_file_name = new_floor_image[len(temp_prefix):].split("?")[0].split("#")[0]
               if temp_file_name and "/" not in temp_file_name and "\\" not in temp_file_name:
                   temp_path = (LAURETTA_TEMP_IMAGES_DIR / temp_file_name).resolve()
                   if (
                       temp_path.is_file()
                       and temp_path.parent == LAURETTA_TEMP_IMAGES_DIR.resolve()
                   ):
                       LAURETTA_CUSTOM_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                       customs_path = (LAURETTA_CUSTOM_IMAGES_DIR / temp_file_name).resolve()
                       if customs_path.parent == LAURETTA_CUSTOM_IMAGES_DIR.resolve():
                           shutil.move(str(temp_path), str(customs_path))
                           # Update params with new customs path
                           new_params["floor_image"] = f"{customs_prefix}{temp_file_name}"
                           target.params = json.dumps(new_params)
                           new_floor_image = new_params["floor_image"]
                           app.logger.info("Moved floor-map image from temp to customs: %s", temp_file_name)

           # Delete old custom image if it was replaced
           if old_floor_image.startswith(customs_prefix) and old_floor_image != new_floor_image:
               old_file_name = old_floor_image[len(customs_prefix):].split("?")[0].split("#")[0]
               if old_file_name and "/" not in old_file_name and "\\" not in old_file_name:
                   old_path = (LAURETTA_CUSTOM_IMAGES_DIR / old_file_name).resolve()
                   if (
                       old_path.is_file()
                       and old_path.parent == LAURETTA_CUSTOM_IMAGES_DIR.resolve()
                   ):
                       old_path.unlink()
                       app.logger.info("Auto-deleted old floor-map image: %s", old_path)

       except Exception as exc:  # noqa: BLE001
           app.logger.warning("Could not handle floor-map image update: %s", exc)

   with app.app_context():
       import sqlalchemy as sqla
       from superset.models.slice import Slice
       sqla.event.listen(Slice, "before_insert", _on_slice_create)
       sqla.event.listen(Slice, "before_delete", _on_slice_delete)
       sqla.event.listen(Slice, "before_update", _on_slice_update)

   # NOTE: these routes are registered straight onto the Flask app, so they bypass
   # FAB's permission layer entirely. @_require_authenticated_user is what keeps
   # them from being anonymously reachable — do not remove it.
   @app.get("/api/v1/lauretta/floors")
   @_require_authenticated_user
   def lauretta_floors_list():
       """Return the list of floors from config.json."""
       config_path = Path("/app/lauretta/dashboards/config.json")
       if not config_path.exists():
           return jsonify([])
       import json as _json
       with open(config_path) as f:
           config = _json.load(f)
       all_floors = []
       seen = set()
       for dash in config.get("dashboards", []):
           for floor in dash.get("floors", []):
               name = floor.get("name", "")
               image = floor.get("image", "")
               if name and name not in seen:
                   seen.add(name)
                   all_floors.append({"name": name, "image": image})
       return jsonify(all_floors)

   @app.get("/api/v1/lauretta/images/<path:floor_ref>")
   @_require_authenticated_user
   def lauretta_floor_image(floor_ref: str):
       image_path = _resolve_floor_image(floor_ref)
       if not image_path:
           return abort(404, description=f"Floor image not found for '{floor_ref}'")
       return send_file(image_path) 

   @app.post("/api/v1/lauretta/images/upload")
   @_require_authenticated_user
   def lauretta_floor_image_upload():
       image_file = request.files.get("file")
       if not image_file or not image_file.filename:
           return abort(400, description="Missing image file")

       original_name = Path(image_file.filename).name
       suffix = Path(original_name).suffix.lower()
       if suffix not in ALLOWED_FLOOR_IMAGE_EXTENSIONS:
           return abort(400, description="Unsupported image extension")

       # The extension alone says nothing about the contents — check the magic bytes
       # match, so we never serve a non-image back under an image Content-Type.
       image_file.stream.seek(0)
       head = image_file.stream.read(32)
       image_file.stream.seek(0)
       if not ALLOWED_FLOOR_IMAGE_EXTENSIONS[suffix](head):
           return abort(
               400,
               description=f"File contents are not a valid {suffix.lstrip('.')} image",
           )

       safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).stem).strip("._")
       if not safe_stem:
           safe_stem = "map_image"

       LAURETTA_TEMP_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
       file_name = f"{safe_stem}_{uuid.uuid4().hex[:10]}{suffix}"
       save_path = (LAURETTA_TEMP_IMAGES_DIR / file_name).resolve()
       if save_path.parent != LAURETTA_TEMP_IMAGES_DIR.resolve():
           return abort(400, description="Invalid image path")

       image_file.save(save_path)

       return jsonify({
           "file_name": file_name,
           "public_url": f"/api/v1/lauretta/images/tmp/{file_name}",
       })

if os.getenv("CYPRESS_CONFIG") == "true":
    # When running the service as a cypress backend, we need to import the config
    # located @ tests/integration_tests/superset_test_config.py
    base_dir = os.path.dirname(__file__)
    module_folder = os.path.abspath(
        os.path.join(base_dir, "../../tests/integration_tests/")
    )
    sys.path.insert(0, module_folder)
    from superset_test_config import *  # noqa

    sys.path.pop(0)

#
# Optionally import superset_config_docker.py (which will have been included on
# the PYTHONPATH) in order to allow for local settings to be overridden
#
try:
    import superset_config_docker
    from superset_config_docker import *  # noqa

    logger.info(
        f"Loaded your Docker configuration at " f"[{superset_config_docker.__file__}]"
    )
except ImportError:
    logger.info("Using default Docker config...")

#
# Hardened overrides for the secure deployment (docker-compose-secure.yml).
#
# Loaded LAST, after superset_config_docker, so that a stale local override file
# can never silently weaken a secure deployment's security settings.
#
# Enabled by SUPERSET_SECURE_MODE=true, which is set only in docker/.env-secure.
# That file also extends PYTHONPATH with /app/docker/pythonpath_secure so this
# import resolves. Modes 1 (dev) and 2 (nondev) never take this branch.
#
if os.getenv("SUPERSET_SECURE_MODE", "").strip().lower() in {"1", "true", "yes"}:
    import superset_config_secure
    from superset_config_secure import *  # noqa

    logger.info(
        f"Loaded hardened secure configuration at "
        f"[{superset_config_secure.__file__}]"
    )
