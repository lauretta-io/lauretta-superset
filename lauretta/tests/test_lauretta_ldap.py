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
# unittest style, as the image has no pytest (PT009/PT027). S105-S107: test passwords.
# ruff: noqa: PT009, PT027, S105, S106, S107
"""Tests of the AD login settings (docker/pythonpath_dev/lauretta_ldap.py).

No directory server: python-ldap is replaced by a stub, so these also run in an image
without it. Run inside the Superset image, from the repo root:

  docker run --rm --network none -v "$PWD":/src:ro -w /src \
    -e PYTHONPATH=/src/docker/pythonpath_dev --entrypoint python \
    <superset image> -m unittest discover -s lauretta/tests -p test_lauretta_ldap.py -v
"""

from __future__ import annotations

import json  # noqa: TID251
import os
import sys
import tempfile
import types
import unittest
from typing import Any
from unittest import mock

import lauretta_ldap as ll

ADMINS = "CN=Superset Admins,OU=Groups,DC=corp,DC=example"
USERS = "CN=Superset Users (RO),OU=Groups,DC=corp,DC=example"
PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


def _fake_ldap() -> types.ModuleType:
    module = types.ModuleType("ldap")
    module.OPT_NETWORK_TIMEOUT = 1  # type: ignore[attr-defined]
    module.OPT_TIMEOUT = 2  # type: ignore[attr-defined]
    module.set_option = mock.Mock()  # type: ignore[attr-defined]
    return module


class LdapSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.conf = self._base()
        self.ldap = _fake_ldap()
        patcher = mock.patch.dict(sys.modules, {"ldap": self.ldap})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _base() -> dict[str, Any]:
        return {
            "servers": ["dc01.corp.example", "dc02.corp.example"],
            "bind_dn": "CN=svc-superset,OU=Service,DC=corp,DC=example",
            "search_base": "DC=corp,DC=example",
            "groups": {ADMINS: ["Admin"], USERS: ["Gamma", "Lauretta Viewer"]},
        }

    def _write(self, password: str = "s3cret!$ ,\n", ca: str = PEM) -> None:
        with open(os.path.join(self.dir, "ldap.json"), "w", encoding="utf-8") as f:
            json.dump(self.conf, f)
        with open(os.path.join(self.dir, "bind_password"), "w", encoding="utf-8") as f:
            f.write(password)
        with open(os.path.join(self.dir, "ca.pem"), "w", encoding="utf-8") as f:
            f.write(ca)

    def _fails(self, fragment: str) -> None:
        with self.assertRaises(ll.LdapConfigError) as ctx:
            ll.ldap_settings(self.dir)
        self.assertIn(fragment, str(ctx.exception))

    def test_off_without_ldap_json(self) -> None:
        self.assertEqual(ll.ldap_settings(self.dir), {})

    def test_directory_from_env(self) -> None:
        self._write()
        with mock.patch.dict(os.environ, {ll.LDAP_DIR_ENV: self.dir}):
            self.assertEqual(ll.ldap_settings()["AUTH_TYPE"], 2)  # AUTH_LDAP

    def test_settings(self) -> None:
        self._write()
        s = ll.ldap_settings(self.dir)
        self.assertEqual(
            s["AUTH_LDAP_SERVER"],
            "ldaps://dc01.corp.example:636 ldaps://dc02.corp.example:636",
        )
        self.assertEqual(s["AUTH_LDAP_BIND_PASSWORD"], "s3cret!$ ,")
        self.assertEqual(
            s["AUTH_LDAP_TLS_CACERTFILE"], os.path.join(self.dir, "ca.pem")
        )
        self.assertTrue(s["AUTH_LDAP_TLS_DEMAND"])
        self.assertFalse(s["AUTH_LDAP_ALLOW_SELF_SIGNED"])
        self.assertFalse(s["AUTH_LDAP_USE_TLS"])
        self.assertEqual(s["AUTH_LDAP_UID_FIELD"], "sAMAccountName")
        self.assertTrue(s["AUTH_USER_REGISTRATION"])
        self.assertEqual(s["AUTH_USER_REGISTRATION_ROLE"], "Public")
        self.assertTrue(s["AUTH_ROLES_SYNC_AT_LOGIN"])
        self.assertEqual(
            s["AUTH_ROLES_MAPPING"],
            {
                "cn=superset admins,ou=groups,dc=corp,dc=example": ["Admin"],
                "cn=superset users (ro),ou=groups,dc=corp,dc=example": [
                    "Gamma",
                    "Lauretta Viewer",
                ],
            },
        )
        # Group DNs escaped inside the filter; disabled accounts excluded.
        self.assertEqual(
            s["AUTH_LDAP_SEARCH_FILTER"],
            "(&(objectClass=user)"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
            f"(|(memberOf={ADMINS})"
            "(memberOf=CN=Superset Users \\28RO\\29,OU=Groups,DC=corp,DC=example)))",
        )
        self.ldap.set_option.assert_any_call(1, ll.NETWORK_TIMEOUT)
        self.ldap.set_option.assert_any_call(2, ll.OPERATION_TIMEOUT)

    def test_port_and_fields(self) -> None:
        self.conf.update(
            port=3269,
            uid_field="userPrincipalName",
            username_pattern=r"[a-z]+@corp\.example",
        )
        self._write()
        s = ll.ldap_settings(self.dir)
        self.assertTrue(
            s["AUTH_LDAP_SERVER"].startswith("ldaps://dc01.corp.example:3269 ")
        )
        self.assertEqual(s["AUTH_LDAP_UID_FIELD"], "userPrincipalName")
        self.assertEqual(s["LAURETTA_LDAP_USERNAME_PATTERN"], r"[a-z]+@corp\.example")

    def test_rejects_bad_config(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"server": "x"}, "unknown keys server"),
            ({"groups": {}}, "missing groups"),
            ({"servers": ["ldap://dc01"]}, "servers must be"),
            ({"servers": "dc01.corp.example"}, "servers must be"),
            ({"port": "636"}, "port must be"),
            ({"port": True}, "port must be"),
            ({"port": 70000}, "port must be"),
            ({"bind_dn": "svc-superset"}, "bind_dn must be a DN"),
            ({"uid_field": "sAM)(x"}, "uid_field"),
            ({"username_pattern": "[a-"}, "username_pattern"),
            ({"groups": [ADMINS]}, "groups must map"),
            ({"groups": {ADMINS: "Admin"}}, "non-empty list"),
            ({"groups": {ADMINS: []}}, "non-empty list"),
            ({"groups": {"Superset Admins": ["Admin"]}}, "is not a DN"),
            (
                {"groups": {ADMINS: ["Admin"], ADMINS.upper(): ["Gamma"]}},
                "listed twice",
            ),
        ]
        for change, fragment in cases:
            with self.subTest(change=change):
                self.conf = self._base()
                self.conf.update(change)
                self._write()
                self._fails(fragment)

    def test_rejects_bad_files(self) -> None:
        self._write(password="\n")
        self._fails("bind_password is empty")
        self._write(ca="not a certificate")
        self._fails("no PEM certificate")
        os.remove(os.path.join(self.dir, "ca.pem"))
        self._fails("cannot read")
        with open(os.path.join(self.dir, "ldap.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self._fails("cannot read")

    def test_needs_python_ldap(self) -> None:
        self._write()
        with mock.patch.dict(sys.modules, {"ldap": None}):
            self._fails("python-ldap is not installed")


class HelpersTest(unittest.TestCase):
    def test_escape_filter_value(self) -> None:
        self.assertEqual(
            ll.escape_filter_value("a*b(c)d\\e\x00"), "a\\2ab\\28c\\29d\\5ce\\00"
        )
        self.assertEqual(ll.escape_filter_value("CN=Plain,DC=x"), "CN=Plain,DC=x")

    def test_normalize_dn(self) -> None:
        self.assertEqual(
            ll.normalize_dn(" CN=Superset Admins, OU=Groups ,DC=Corp "),
            "cn=superset admins,ou=groups,dc=corp",
        )
        self.assertEqual(ll.normalize_dn("cn = A + uid = b"), "cn=a+uid=b")

    def test_username_allowed(self) -> None:
        self.assertTrue(ll.username_allowed("j.doe-2_x", "pw"))
        for name in ("jdo*", "j)(x", "a\\b", "a\x00", "j doe", "x" * 21, ""):
            with self.subTest(name=name):
                self.assertFalse(ll.username_allowed(name, "pw"))
        self.assertFalse(ll.username_allowed("jdoe", ""))
        self.assertFalse(ll.username_allowed(None, "pw"))
        # A permissive pattern still never lets filter metacharacters through.
        self.assertTrue(ll.username_allowed("j doe", "pw", ".+"))
        self.assertFalse(ll.username_allowed("jdo*", "pw", ".+"))


if __name__ == "__main__":
    unittest.main()
