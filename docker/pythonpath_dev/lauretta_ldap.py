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
"""
Active Directory login over LDAPS (lauretta/docs/13-ad-login.md).

Off unless the directory LAURETTA_LDAP_DIR (default /app/ldap) holds `ldap.json`.
docker-compose-ha.yml mounts SUPERSET_LDAP_DIR there; unset, it mounts an empty
directory and Superset keeps its own password login. The directory holds:

  ldap.json       which servers, which account searches, which groups get which roles
  bind_password   the search account's password (the whole file, one trailing newline
                  allowed); a file rather than an env var because raib-ha's env files
                  only carry [A-Za-z0-9+/=._~:-]
  ca.pem          the CA chain (PEM) that signed the domain controllers' certificates

Only members of the groups in ldap.json can log in, and the search filter skips
disabled accounts. Roles come from those groups on every login. A broken directory
stops the config from loading rather than quietly falling back to password login.
"""

from __future__ import annotations

import json  # noqa: TID251
import os
import re
from typing import Any

LDAP_DIR_ENV = "LAURETTA_LDAP_DIR"
DEFAULT_LDAP_DIR = "/app/ldap"
CONF_FILE = "ldap.json"
PASSWORD_FILE = "bind_password"  # noqa: S105 -- a file name, not a password
CA_FILE = "ca.pem"

# sAMAccountName is at most 20 characters. Override with "username_pattern" if the
# domain allows other characters; the filter metacharacters below stay refused.
DEFAULT_USERNAME_PATTERN = r"[A-Za-z0-9._-]{1,20}"
# RFC 4515 filter metacharacters. Flask-AppBuilder puts the typed username into the
# search filter unescaped: "jdo*" would match jdoe and register a second Superset
# account named "jdo*", outside any deactivation of "jdoe".
_FILTER_SPECIALS = frozenset("*()\\\x00")

# Seconds. Flask-AppBuilder sets no timeouts: an unreachable DC (firewall drop) would
# hold a login for the kernel's TCP timeout before the next server is tried, longer
# than gunicorn waits for the request.
NETWORK_TIMEOUT = 5
OPERATION_TIMEOUT = 15

_KEYS = {
    "servers",
    "port",
    "bind_dn",
    "search_base",
    "uid_field",
    "username_pattern",
    "groups",
}
_REQUIRED = {"servers", "bind_dn", "search_base", "groups"}
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_ATTRIBUTE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}")
_DN_SEPARATORS = re.compile(r"\s*([,=+])\s*")
# AD: userAccountControl bit 2 (ACCOUNTDISABLE), via the bitwise-AND matching rule.
_ENABLED_ONLY = "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"


class LdapConfigError(ValueError):
    """The LDAP directory is present but unusable."""


def escape_filter_value(value: str) -> str:
    """RFC 4515 escaping for a value inside an LDAP search filter."""
    out = []
    for char in value:
        out.append(f"\\{ord(char):02x}" if char in _FILTER_SPECIALS else char)
    return "".join(out)


def normalize_dn(dn: str) -> str:
    """Comparable form of a DN: lower case, no spaces around `,` `=` `+`.

    AD compares DNs case-insensitively, so the search filter accepts a group DN typed
    in any case, while Flask-AppBuilder maps roles by exact string. Both sides of
    that mapping go through this function (see LaurettaSecurityManager).
    """
    return _DN_SEPARATORS.sub(r"\1", dn.strip()).lower()


def username_allowed(
    username: str | None, password: str | None, pattern: str = DEFAULT_USERNAME_PATTERN
) -> bool:
    """Whether a login attempt may go to the directory at all.

    An empty password is refused here too: AD treats a simple bind with a DN and an
    empty password as an anonymous bind and reports success.
    """
    if not username or not password:
        return False
    if any(char in _FILTER_SPECIALS for char in username):
        return False
    return re.fullmatch(pattern, username) is not None


def _fail(message: str) -> LdapConfigError:
    return LdapConfigError(f"AD login ({CONF_FILE}): {message}")


def _read_conf(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            conf = json.load(handle)
    except (OSError, ValueError) as ex:
        raise _fail(f"cannot read {path}: {ex}") from None
    if not isinstance(conf, dict):
        raise _fail("must be a JSON object")
    if unknown := sorted(set(conf) - _KEYS):
        raise _fail(f"unknown keys {', '.join(unknown)}")
    missing = sorted(key for key in _REQUIRED if not conf.get(key))
    if missing:
        raise _fail(f"missing {', '.join(missing)}")
    return conf


def _servers(conf: dict[str, Any]) -> str:
    servers = conf["servers"]
    if not isinstance(servers, list) or not all(
        isinstance(host, str) and _HOST.fullmatch(host) for host in servers
    ):
        raise _fail('servers must be a list of host names, e.g. ["dc01.corp.example"]')
    port = conf.get("port", 636)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise _fail("port must be a TCP port number")
    # libldap tries a space-separated list of URIs in order.
    return " ".join(f"ldaps://{host}:{port}" for host in servers)


def _groups(conf: dict[str, Any]) -> dict[str, list[str]]:
    groups = conf["groups"]
    if not isinstance(groups, dict):
        raise _fail("groups must map group DNs to lists of Superset role names")
    mapping: dict[str, list[str]] = {}
    for dn, roles in groups.items():
        if not dn.strip() or "=" not in dn:
            raise _fail(f"groups: {dn!r} is not a DN")
        if (
            not isinstance(roles, list)
            or not roles
            or not all(isinstance(role, str) and role.strip() for role in roles)
        ):
            raise _fail(f"groups: {dn!r} needs a non-empty list of role names")
        key = normalize_dn(dn)
        if key in mapping:
            raise _fail(f"groups: {dn!r} is listed twice")
        mapping[key] = roles
    return mapping


def _search_filter(groups: list[str]) -> str:
    members = "".join(f"(memberOf={escape_filter_value(dn)})" for dn in groups)
    return f"(&(objectClass=user){_ENABLED_ONLY}(|{members}))"


def _read_password(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            password = handle.read()
    except OSError as ex:
        raise _fail(f"cannot read {path}: {ex}") from None
    if password.endswith("\n"):
        password = password[:-1]
    if password.endswith("\r"):
        password = password[:-1]
    if not password:
        raise _fail(f"{path} is empty")
    return password


def _check_ca(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as handle:
            pem = handle.read()
    except (OSError, UnicodeDecodeError) as ex:
        raise _fail(f"cannot read {path}: {ex}") from None
    if "-----BEGIN CERTIFICATE-----" not in pem:
        raise _fail(
            f"{path} holds no PEM certificate (convert a DER .cer with "
            "`openssl x509 -inform der -in ca.cer -out ca.pem`)"
        )


def ldap_settings(directory: str | None = None) -> dict[str, Any]:
    """Superset config keys for AD login, or {} when the directory has no ldap.json."""
    directory = directory or os.environ.get(LDAP_DIR_ENV) or DEFAULT_LDAP_DIR
    conf_path = os.path.join(directory, CONF_FILE)
    try:
        os.stat(conf_path)
    except FileNotFoundError:
        return {}
    except OSError as ex:
        # Not os.path.exists: it answers False on EACCES too, and a directory the
        # container cannot read would quietly leave password login on.
        raise _fail(f"cannot read {conf_path}: {ex}") from None

    conf = _read_conf(conf_path)
    server = _servers(conf)
    for key in ("bind_dn", "search_base"):
        if not isinstance(conf[key], str) or "=" not in conf[key]:
            raise _fail(f'{key} must be a DN, e.g. "DC=corp,DC=example"')
    uid_field = conf.get("uid_field", "sAMAccountName")
    if not isinstance(uid_field, str) or not _ATTRIBUTE.fullmatch(uid_field):
        raise _fail("uid_field must be an attribute name")
    pattern = conf.get("username_pattern", DEFAULT_USERNAME_PATTERN)
    try:
        re.compile(pattern)
    except (re.error, TypeError) as ex:
        raise _fail(f"username_pattern: {ex}") from None
    mapping = _groups(conf)
    password = _read_password(os.path.join(directory, PASSWORD_FILE))
    ca_path = os.path.join(directory, CA_FILE)
    _check_ca(ca_path)

    try:
        import ldap
    except ImportError:
        raise _fail(
            "python-ldap is not installed in this image; rebuild it from the "
            "Dockerfile's production stage"
        ) from None
    ldap.set_option(ldap.OPT_NETWORK_TIMEOUT, NETWORK_TIMEOUT)
    ldap.set_option(ldap.OPT_TIMEOUT, OPERATION_TIMEOUT)

    from flask_appbuilder.security.manager import AUTH_LDAP

    return {
        "AUTH_TYPE": AUTH_LDAP,
        "AUTH_LDAP_SERVER": server,
        # TLS comes from ldaps://. True would mean StartTLS on top of it.
        "AUTH_LDAP_USE_TLS": False,
        # Verify the DC certificate against ca.pem, host name included. Also the
        # only branch where Flask-AppBuilder resets the TLS context, so the CA file
        # reliably takes effect.
        "AUTH_LDAP_TLS_DEMAND": True,
        "AUTH_LDAP_ALLOW_SELF_SIGNED": False,
        "AUTH_LDAP_TLS_CACERTFILE": ca_path,
        "AUTH_LDAP_BIND_USER": conf["bind_dn"],
        "AUTH_LDAP_BIND_PASSWORD": password,
        "AUTH_LDAP_SEARCH": conf["search_base"],
        "AUTH_LDAP_SEARCH_FILTER": _search_filter(list(conf["groups"])),
        "AUTH_LDAP_UID_FIELD": uid_field,
        "AUTH_LDAP_FIRSTNAME_FIELD": "givenName",
        "AUTH_LDAP_LASTNAME_FIELD": "sn",
        "AUTH_LDAP_EMAIL_FIELD": "mail",
        "AUTH_LDAP_GROUP_FIELD": "memberOf",
        "AUTH_ROLES_MAPPING": mapping,
        # AD is the source of truth: roles edited in Superset revert at next login.
        "AUTH_ROLES_SYNC_AT_LOGIN": True,
        # Creates the Superset user at the first AD login. The registration role is
        # added to every AD user, so it is Public, which secure mode keeps empty.
        "AUTH_USER_REGISTRATION": True,
        "AUTH_USER_REGISTRATION_ROLE": "Public",
        "LAURETTA_LDAP_USERNAME_PATTERN": pattern,
    }


def check(username: str, directory: str | None = None) -> int:
    """Connect as the search account and show what a login by `username` would get.

    Exercises DNS, the TLS chain, the bind password, the search base and the group
    filter, without the user's password and without touching Superset's database.
    """
    settings = ldap_settings(directory)
    if not settings:
        print(
            f"AD login is off: no {CONF_FILE} in "
            f"{directory or os.environ.get(LDAP_DIR_ENV) or DEFAULT_LDAP_DIR}"
        )
        return 1
    import ldap

    ldap.set_option(ldap.OPT_X_TLS_CACERTFILE, settings["AUTH_LDAP_TLS_CACERTFILE"])
    ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_DEMAND)
    ldap.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
    con = ldap.initialize(settings["AUTH_LDAP_SERVER"])
    con.set_option(ldap.OPT_REFERRALS, 0)
    try:
        con.simple_bind_s(
            settings["AUTH_LDAP_BIND_USER"], settings["AUTH_LDAP_BIND_PASSWORD"]
        )
    except ldap.LDAPError as ex:
        print(
            f"FAIL bind as {settings['AUTH_LDAP_BIND_USER']} via "
            f"{settings['AUTH_LDAP_SERVER']}: {ex}"
        )
        return 1
    print(f"ok   bind as {settings['AUTH_LDAP_BIND_USER']}")
    if not username_allowed(username, "-", settings["LAURETTA_LDAP_USERNAME_PATTERN"]):
        print(
            f"FAIL {username!r} does not match username_pattern; the login is refused"
        )
        return 1
    uid = settings["AUTH_LDAP_UID_FIELD"]
    found = [
        (dn, attrs)
        for dn, attrs in con.search_s(
            settings["AUTH_LDAP_SEARCH"],
            ldap.SCOPE_SUBTREE,
            f"(&{settings['AUTH_LDAP_SEARCH_FILTER']}({uid}={username}))",
            ["givenName", "sn", "mail", "memberOf"],
        )
        if dn
    ]
    if len(found) != 1:
        print(
            f"FAIL {username!r}: {len(found)} matches. Not in a mapped group as a "
            "direct member, disabled, or outside the search base; the login is refused"
        )
        return 1
    dn, attrs = found[0]
    groups = [value.decode() for value in attrs.get("memberOf", [])]
    roles = sorted(
        {
            role
            for group in groups
            for role in settings["AUTH_ROLES_MAPPING"].get(normalize_dn(group), [])
        }
    )
    mail = b",".join(attrs.get("mail", [])).decode() or (
        f"(none: Superset stores {username}@email.notfound)"
    )
    print(
        f"ok   {username!r} is {dn}\n     mail {mail}\n     roles {roles + ['Public']}"
    )
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "check":
        sys.exit("usage: python -m lauretta_ldap check <username>")
    sys.exit(check(sys.argv[2]))
