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
Authorization for the fork's /api/v1/lauretta/* routes (superset_config.py).

Those routes are registered on the Flask app, outside Flask-AppBuilder, so they get no
permission check of their own. The rules, in terms of Superset's own data access:

* A property is one `dashboards[]` entry of lauretta/dashboards/config.json. Its
  database is the one docker/import-dashboards.py created, found by the same
  path-derived UUID. The caller can read the property if they can access that
  database, or any dataset in it (datasource, schema or catalog access).
* GET /floors lists only the floors of properties the caller can read.
* GET /images/<ref>, by where the resolved file lives:
    - lauretta/images/<file> named by a config.json floor: readable by callers who
      can read one of the properties naming it. Charts do not unlock these files:
      any chart writer could otherwise point a chart at another property's
      (guessable) floor plan and read it.
    - lauretta/images/<file> no floor names, and customs/<file>: readable by
      callers who can access (Superset's can_access_chart) a floor-map chart whose
      `floor_image` resolves to the file.
    - tmp/<file>: readable by the session that uploaded it.
  Admin reads everything. A denied image answers exactly like a missing one (404).
* POST /images/upload needs can_write on Chart plus access to at least one dataset,
  i.e. what it takes to build a floor-map chart.

The policy functions take their Superset lookups as arguments so they can be tested
without an app; FloorAccess binds them to Superset for one request.
"""

from __future__ import annotations

import json  # noqa: TID251 -- loaded from superset_config, before superset.utils
import logging
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

logger = logging.getLogger(__name__)

FLOOR_MAP_VIZ_TYPE = "ext-floor-map"
# Flask session key holding the tmp/ file names this session uploaded.
TEMP_UPLOADS_SESSION_KEY = "lauretta_temp_uploads"
# Kept short: with the default cookie sessions the list travels in the cookie.
MAX_REMEMBERED_TEMP_UPLOADS = 20

Resolver = Callable[[str], Optional[Path]]
PropertyCheck = Callable[[Optional[str]], bool]


def property_database_uuid(dashboard_path: str) -> str:
    """UUID of the database import-dashboards.py creates for a config.json entry.

    Must stay identical to generate_database_uuid() in docker/import-dashboards.py
    (lauretta/tests/test_lauretta_access.py checks that it does).
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lauretta-db::{dashboard_path}"))


def load_dashboards(config_path: Path) -> list[dict[str, Any]]:
    """The `dashboards` entries of config.json; [] when the file does not exist."""
    if not config_path.exists():
        return []
    with open(config_path, encoding="utf-8") as config_file:
        config = json.load(config_file)
    dashboards = config.get("dashboards", []) if isinstance(config, dict) else []
    return [dash for dash in dashboards if isinstance(dash, dict)]


def dashboard_property(dashboard: dict[str, Any]) -> Optional[str]:
    """The property (database UUID) of a config.json entry; None without a path."""
    path = dashboard.get("path")
    if isinstance(path, str) and path:
        return property_database_uuid(path)
    return None


def _floors(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    floors = dashboard.get("floors") or []
    return [floor for floor in floors if isinstance(floor, dict)]


def visible_floors(
    dashboards: Iterable[dict[str, Any]], may_read_property: PropertyCheck
) -> list[dict[str, str]]:
    """[{name, image}] for the floors of readable properties, first name wins."""
    visible: list[dict[str, str]] = []
    seen: set[str] = set()
    for dashboard in dashboards:
        if not may_read_property(dashboard_property(dashboard)):
            continue
        for floor in _floors(dashboard):
            name = floor.get("name", "")
            if name and name not in seen:
                seen.add(name)
                visible.append({"name": name, "image": floor.get("image", "")})
    return visible


def _resolves_to(ref: Any, target: Path, resolve: Resolver) -> bool:
    if not isinstance(ref, str) or not ref.strip():
        return False
    # The route sees the URL-decoded path; a stored ref may still be encoded.
    resolved = resolve(unquote(ref))
    return resolved is not None and resolved.resolve() == target.resolve()


def claiming_properties(
    target: Path, dashboards: Iterable[dict[str, Any]], resolve: Resolver
) -> set[Optional[str]]:
    """Properties with a config.json floor whose `image` resolves to `target`."""
    return {
        dashboard_property(dashboard)
        for dashboard in dashboards
        for floor in _floors(dashboard)
        if _resolves_to(floor.get("image"), target, resolve)
    }


def chart_floor_image(params: Optional[str]) -> str:
    """The `floor_image` of a chart's params JSON; '' when absent or unparsable."""
    try:
        parsed = json.loads(params or "{}")
    except ValueError:
        return ""
    ref = parsed.get("floor_image") if isinstance(parsed, dict) else None
    return ref.strip() if isinstance(ref, str) else ""


def image_area(target: Path, customs_dir: Path, tmp_dir: Path) -> str:
    """'tmp', 'customs' or 'base' (lauretta/images itself), by the file's directory."""
    parent = target.resolve().parent
    if parent == tmp_dir.resolve():
        return "tmp"
    if parent == customs_dir.resolve():
        return "customs"
    return "base"


def may_read_image(
    target: Path,
    *,
    customs_dir: Path,
    tmp_dir: Path,
    dashboards: Iterable[dict[str, Any]],
    resolve: Resolver,
    may_read_property: PropertyCheck,
    readable_chart_references: Callable[[Path], bool],
    own_temp_uploads: Iterable[str],
) -> bool:
    """The image rule from the module docstring, for a non-admin caller."""
    area = image_area(target, customs_dir, tmp_dir)
    if area == "tmp":
        return target.name in set(own_temp_uploads)
    if area == "base":
        claimed = claiming_properties(target, dashboards, resolve)
        if claimed:
            return any(may_read_property(prop) for prop in claimed)
    return readable_chart_references(target)


def remember_temp_upload(file_name: str) -> None:
    """Record in the session that this session uploaded tmp/<file_name>."""
    from flask import session

    names = [n for n in session.get(TEMP_UPLOADS_SESSION_KEY, []) if n != file_name]
    names.append(file_name)
    session[TEMP_UPLOADS_SESSION_KEY] = names[-MAX_REMEMBERED_TEMP_UPLOADS:]


def private_image_response(response: Any) -> Any:
    """Stop shared caches from storing an image another caller may not see.

    send_file applies SEND_FILE_MAX_AGE_DEFAULT (a year, `public`). no-cache makes the
    browser revalidate, so the access check runs again and the ETag still saves the
    transfer (304) when nothing changed.
    """
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers.pop("Expires", None)
    return response


class FloorAccess:
    """The rules above for the current user, bound to Superset, for one request."""

    def __init__(
        self,
        *,
        config_path: Path,
        customs_dir: Path,
        tmp_dir: Path,
        resolve: Resolver,
    ) -> None:
        from superset import security_manager

        self._sm = security_manager
        self._config_path = config_path
        self._customs_dir = customs_dir
        self._tmp_dir = tmp_dir
        self._resolve = resolve
        self._dashboards: Optional[list[dict[str, Any]]] = None
        self._property_access: dict[Optional[str], bool] = {}
        self._floor_map_charts: Optional[list[Any]] = None
        self.is_admin = bool(security_manager.is_admin())
        self.sees_all_data = self.is_admin or bool(
            security_manager.can_access_all_datasources()
        )

    @property
    def dashboards(self) -> list[dict[str, Any]]:
        if self._dashboards is None:
            self._dashboards = load_dashboards(self._config_path)
        return self._dashboards

    def floors(self) -> list[dict[str, str]]:
        return visible_floors(self.dashboards, self.may_read_property)

    def may_read_property(self, prop: Optional[str]) -> bool:
        if self.sees_all_data:
            return True
        if prop is None:
            return False
        if prop not in self._property_access:
            self._property_access[prop] = self._database_readable(prop)
        return self._property_access[prop]

    def _database_readable(self, prop: str) -> bool:
        from superset import db
        from superset.models.core import Database

        database = (
            db.session.query(Database)
            .filter(Database.uuid == uuid.UUID(prop))
            .one_or_none()
        )
        if database is None:
            return False
        return bool(self._sm.can_access_database(database)) or self._any_dataset(
            database.id
        )

    @staticmethod
    def _any_dataset(database_id: Optional[int] = None) -> bool:
        """Whether some dataset (of `database_id`, if given) is accessible."""
        from superset import db
        from superset.connectors.sqla.models import SqlaTable
        from superset.models.core import Database
        from superset.utils.filters import get_dataset_access_filters

        # The filter refers to Database.id, hence the join (as DatasourceFilter).
        query = (
            db.session.query(SqlaTable.id)
            .join(Database, Database.id == SqlaTable.database_id)
            .filter(get_dataset_access_filters(SqlaTable))
        )
        if database_id is not None:
            query = query.filter(SqlaTable.database_id == database_id)
        return query.first() is not None

    def may_read_image(self, target: Path) -> bool:
        """Fails closed: an error while checking denies (and is logged)."""
        if self.is_admin:
            return True
        try:
            return may_read_image(
                target,
                customs_dir=self._customs_dir,
                tmp_dir=self._tmp_dir,
                dashboards=self.dashboards,
                resolve=self._resolve,
                may_read_property=self.may_read_property,
                readable_chart_references=self._readable_chart_references,
                own_temp_uploads=self._own_temp_uploads(),
            )
        except Exception:  # noqa: BLE001 -- any failure means no access
            logger.warning("Floor image access check failed; denying", exc_info=True)
            return False

    def _readable_chart_references(self, target: Path) -> bool:
        if self._floor_map_charts is None:
            from superset import db
            from superset.models.slice import Slice

            # All floor-map charts, not a LIKE on the file name: a chart may name its
            # image by floor code ("CF") or URL-encoded. There are few such charts.
            self._floor_map_charts = (
                db.session.query(Slice)
                .filter(Slice.viz_type == FLOOR_MAP_VIZ_TYPE)
                .all()
            )
        return any(
            _resolves_to(chart_floor_image(chart.params), target, self._resolve)
            and self._sm.can_access_chart(chart)
            for chart in self._floor_map_charts
        )

    @staticmethod
    def _own_temp_uploads() -> list[str]:
        from flask import session

        names = session.get(TEMP_UPLOADS_SESSION_KEY, [])
        return [n for n in names if isinstance(n, str)]

    def may_upload(self) -> bool:
        """can_write on Chart, and some data to build a floor-map chart on."""
        if self.is_admin:
            return True
        if not self._sm.can_access("can_write", "Chart"):
            return False
        return self.sees_all_data or self._any_dataset()
