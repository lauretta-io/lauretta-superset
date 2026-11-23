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
"""Tests of the /api/v1/lauretta/* access rules (lauretta_access.py).

The module lives in docker/pythonpath_dev. No Superset app or database: the policy
functions take their lookups as arguments, and the FloorAccess cases replace the
security manager. Run inside the Superset image (it has PyYAML, which
docker/import-dashboards.py needs), from the repo root:

  docker run --rm --network none -v "$PWD":/src:ro -w /src \
    -e PYTHONPATH=/src/docker/pythonpath_dev --entrypoint python \
    <superset image> -m unittest discover -s lauretta/tests -v
"""

from __future__ import annotations

import importlib.util
import json  # noqa: TID251
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest import mock

import lauretta_access as la

REPO_ROOT = Path(__file__).resolve().parents[2]
PATH_A = "/lauretta/dashboards/property_a.zip"
PATH_B = "/lauretta/dashboards/property_b.zip"
PROP_A = la.property_database_uuid(PATH_A)
PROP_B = la.property_database_uuid(PATH_B)

DASHBOARDS: list[dict[str, Any]] = [
    {
        "path": PATH_A,
        "floors": [
            {"id": 1, "name": "GF", "image": "A_plan_GF.png"},
            {"id": 2, "name": "L1", "image": "/api/v1/lauretta/images/A_plan_L1.png"},
        ],
    },
    {
        "path": PATH_B,
        "floors": [
            {"id": 1, "name": "GF", "image": "B_plan_GF.png"},
            {"id": 2, "name": "B2", "image": "B_plan_B2.png"},
        ],
    },
    {"floors": [{"id": 9, "name": "NOPATH", "image": "A_plan_GF.png"}]},
]


class Images:
    """A lauretta/images tree and a simplified stand-in for _resolve_floor_image."""

    def __init__(self, root: Path) -> None:
        self.base = root
        self.customs = root / "customs"
        self.tmp = root / "tmp"
        for directory in (self.customs, self.tmp):
            directory.mkdir(parents=True)
        for name in (
            "A_plan_GF.png",
            "A_plan_L1.png",
            "B_plan_GF.png",
            "B_plan_B2.png",
        ):
            (self.base / name).write_bytes(b"x")
        (self.base / "loose.png").write_bytes(b"x")
        (self.customs / "c1.png").write_bytes(b"x")
        (self.tmp / "t1.png").write_bytes(b"x")

    def resolve(self, ref: str) -> Optional[Path]:
        ref = ref.strip().lstrip("/").removeprefix("api/v1/lauretta/images/")
        candidate = (self.base / ref).resolve()
        return candidate if candidate.is_file() else None


class PolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.images = Images(Path(self._dir.name))

    def tearDown(self) -> None:
        self._dir.cleanup()

    def may_read(
        self,
        name: str,
        *,
        props: frozenset[Optional[str]] = frozenset(),
        chart_ok: bool = False,
        own: tuple[str, ...] = (),
    ) -> bool:
        return la.may_read_image(
            (self.images.base / name).resolve(),
            customs_dir=self.images.customs,
            tmp_dir=self.images.tmp,
            dashboards=DASHBOARDS,
            resolve=self.images.resolve,
            may_read_property=lambda prop: prop in props,
            readable_chart_references=lambda _target: chart_ok,
            own_temp_uploads=own,
        )

    def test_uuid_matches_import_dashboards(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "import_dashboards", REPO_ROOT / "docker" / "import-dashboards.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for path in (PATH_A, PATH_B, "/lauretta/dashboards/x_v{}.zip"):
            assert la.property_database_uuid(path) == module.generate_database_uuid(
                path, {}
            )

    def test_floors_only_of_readable_properties(self) -> None:
        only_b = la.visible_floors(DASHBOARDS, lambda prop: prop == PROP_B)
        assert only_b == [
            {"name": "GF", "image": "B_plan_GF.png"},
            {"name": "B2", "image": "B_plan_B2.png"},
        ]
        assert la.visible_floors(DASHBOARDS, lambda prop: False) == []

    def test_floors_first_name_wins_and_no_path_needs_all_access(self) -> None:
        every = la.visible_floors(DASHBOARDS, lambda prop: True)
        assert [floor["name"] for floor in every] == ["GF", "L1", "B2", "NOPATH"]
        assert every[0]["image"] == "A_plan_GF.png"
        some = la.visible_floors(DASHBOARDS, lambda prop: prop is not None)
        assert "NOPATH" not in [floor["name"] for floor in some]

    def test_claimed_image_follows_its_property(self) -> None:
        assert self.may_read("A_plan_GF.png", props=frozenset({PROP_A}))
        assert self.may_read("A_plan_L1.png", props=frozenset({PROP_A}))
        assert not self.may_read("A_plan_GF.png", props=frozenset({PROP_B}))
        assert self.may_read("B_plan_B2.png", props=frozenset({PROP_B}))

    def test_chart_does_not_unlock_a_claimed_image(self) -> None:
        assert not self.may_read(
            "A_plan_GF.png", props=frozenset({PROP_B}), chart_ok=True
        )

    def test_unclaimed_and_custom_images_follow_charts(self) -> None:
        for name in ("loose.png", "customs/c1.png"):
            assert not self.may_read(name, props=frozenset({PROP_A, PROP_B}))
            assert self.may_read(name, chart_ok=True)

    def test_tmp_image_only_for_its_uploader(self) -> None:
        assert self.may_read("tmp/t1.png", own=("t1.png",))
        assert not self.may_read("tmp/t1.png", own=("other.png",))
        everything = frozenset({PROP_A, PROP_B})
        assert not self.may_read("tmp/t1.png", props=everything, chart_ok=True)

    def test_chart_floor_image(self) -> None:
        params = json.dumps({"floor_image": " /x.png "})
        assert la.chart_floor_image(params) == "/x.png"
        for bad in (None, "", "not json", "[1]", json.dumps({"floor_image": 3})):
            assert la.chart_floor_image(bad) == ""

    def test_encoded_chart_reference_resolves(self) -> None:
        (self.images.base / "a b.png").write_bytes(b"x")
        target = (self.images.base / "a b.png").resolve()
        assert la._resolves_to("a%20b.png", target, self.images.resolve)
        assert not la._resolves_to("", target, self.images.resolve)

    def test_missing_config(self) -> None:
        assert la.load_dashboards(Path(self._dir.name) / "none.json") == []


class FloorAccessTest(unittest.TestCase):
    """The Superset-bound wrapper: admin bypass, fail-closed, upload rule."""

    def access(self, **sm_attrs: object) -> la.FloorAccess:
        sm = mock.MagicMock()
        sm.is_admin.return_value = False
        sm.can_access_all_datasources.return_value = False
        for name, value in sm_attrs.items():
            getattr(sm, name).return_value = value
        fake_superset = types.ModuleType("superset")
        fake_superset.__dict__["security_manager"] = sm
        with mock.patch.dict(sys.modules, {"superset": fake_superset}):
            return la.FloorAccess(
                config_path=Path("/nonexistent/config.json"),
                customs_dir=Path("/nonexistent/customs"),
                tmp_dir=Path("/nonexistent/tmp"),
                resolve=lambda _ref: None,
            )

    def test_admin_reads_and_uploads(self) -> None:
        access = self.access(is_admin=True)
        assert access.may_read_image(Path("/anything.png"))
        assert access.may_upload()

    def test_errors_deny(self) -> None:
        access = self.access()
        with (
            mock.patch.object(la, "may_read_image", side_effect=RuntimeError),
            mock.patch.object(la.logger, "warning") as warning,
        ):
            assert not access.may_read_image(Path("/x.png"))
        warning.assert_called_once()

    def test_upload_needs_chart_write_and_some_data(self) -> None:
        cases = [
            # can_write on Chart, some dataset, expected
            (False, True, False),
            (True, False, False),
            (True, True, True),
        ]
        for can_write, has_data, expected in cases:
            access = self.access(can_access=can_write)
            with mock.patch.object(
                la.FloorAccess, "_any_dataset", return_value=has_data
            ):
                assert access.may_upload() == expected, (can_write, has_data)

    def test_all_datasource_access_reads_every_property(self) -> None:
        access = self.access(can_access_all_datasources=True)
        assert access.may_read_property(PROP_A)
        assert access.may_read_property(None)


if __name__ == "__main__":
    unittest.main()
