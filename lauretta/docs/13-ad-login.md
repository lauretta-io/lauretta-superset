# 13 Active Directory login

**For:** whoever connects a secure or HA deployment to a customer's Active Directory, so that
members of chosen AD groups log in with their domain accounts.

Superset checks the password against AD over **LDAPS** (port 636, certificate verified).
Only enabled accounts that are **direct members** of the groups you list can log in. Their
Superset role comes from those groups and is refreshed at every login. A user gets a Superset
account at their first login. Users don't need a VPN or Windows sign-in; they type their AD
username and password on the normal login page.

Code: `docker/pythonpath_dev/lauretta_ldap.py` (settings, checks, `check` command), the login
check in `lauretta_audit.py` (`LaurettaSecurityManager.auth_user_ldap`), the hook at the end of
`superset_config_secure.py`, and the `/app/ldap` mount in `docker-compose-ha.yml`.

## What to get from the AD team

| # | Item | Notes |
|---|---|---|
| 1 | Host names (FQDN) of one or more domain controllers | Names, not addresses: the name must be on the DC certificate. Two or more for failover. |
| 2 | Port 636 open from every Superset node to those DCs | Give them the nodes' addresses. |
| 3 | The CA chain (PEM) that signed the DC certificates | Root plus intermediates. Ask when the DC certificates expire. |
| 4 | A read-only search account: its full DN and password | Interactive logon denied. Agree how its password is rotated. |
| 5 | The search base DN | e.g. `DC=corp,DC=example,DC=local`, or a narrower OU |
| 6 | The login attribute, and the characters usernames may contain | Usually `sAMAccountName` (at most 20 characters) |
| 7 | The exact DN of each group that may log in, and the Superset role(s) for each | Copy the DNs from real `ldapsearch` output; don't retype them. |
| 8 | Confirmation that users are **direct** members of those groups | Members only through a nested group are refused. |
| 9 | Confirmation that `mail` is filled in and unique | Superset refuses a second account with the same email. |
| 10 | Test accounts | One per group, one domain user in no group, and one disabled account |

## 1. The image needs python-ldap

The `production` stage of the `Dockerfile` installs `python-ldap`; images built before it
do not have it. With AD login configured on such an image, the config refuses to load
("python-ldap is not installed"). Check:

```bash
docker run --rm --entrypoint python <image> -c "import ldap; print(ldap.__version__)"   # 3.4.4
```

Otherwise rebuild the secure image ([06](06-secure-mode.md), [10](10-development.md)). Under
raib-ha that is a new local tag, a new `SUPERSET_IMAGE_DIGEST`, and the same image on every
node ([11](11-ha-mode.md#under-raib-ha-the-two-node-raib-cluster)).

## 2. The LDAP directory

One directory on every node, **outside the checkout**, e.g. `/etc/lauretta-superset/ldap`:

```
ldap.json       settings (below)
bind_password   the search account's password, nothing else (one trailing newline is fine)
ca.pem          the CA chain from item 3, PEM
```

```json
{
  "servers": ["dc01.corp.example.local", "dc02.corp.example.local"],
  "bind_dn": "CN=svc-superset,OU=Service Accounts,DC=corp,DC=example,DC=local",
  "search_base": "DC=corp,DC=example,DC=local",
  "groups": {
    "CN=Superset Admins,OU=Groups,DC=corp,DC=example,DC=local": ["Admin"],
    "CN=Superset Users,OU=Groups,DC=corp,DC=example,DC=local": ["Gamma", "<your data-access role>"]
  }
}
```

Optional keys: `port` (default 636), `uid_field` (default `sAMAccountName`),
`username_pattern` (default `[A-Za-z0-9._-]{1,20}`; `*`, `(`, `)`, `\` and NUL are always
refused). Any other key is an error. `servers` are tried in order, with a 5-second connect
timeout each. A DER `.cer` becomes PEM with
`openssl x509 -inform der -in ca.cer -out ca.pem`.

The container runs as uid 1000 and must be able to read all three files; nobody else needs to:

```bash
sudo install -d -o root -g 1000 -m 0750 /etc/lauretta-superset/ldap
sudo install -o root -g 1000 -m 0640 ldap.json ca.pem /etc/lauretta-superset/ldap/
sudo sh -c 'umask 027; cat > /etc/lauretta-superset/ldap/bind_password' && \
  sudo chgrp 1000 /etc/lauretta-superset/ldap/bind_password   # type it, then Ctrl-D
```

Roles named in `groups` must exist in Superset (built-in or created under Settings → List
Roles). `Gamma` alone shows no data: add the role that grants dataset access.

## 3. Check from the node, then from the container

On the node (`sudo apt-get install -y ldap-utils openssl`):

```bash
openssl s_client -connect dc01.corp.example.local:636 -CAfile /etc/lauretta-superset/ldap/ca.pem \
  -verify_hostname dc01.corp.example.local </dev/null 2>/dev/null | grep "Verify return code"
# Verify return code: 0 (ok)
LDAPTLS_CACERT=/etc/lauretta-superset/ldap/ca.pem ldapsearch -x -H ldaps://dc01.corp.example.local \
  -D 'CN=svc-superset,OU=Service Accounts,DC=corp,DC=example,DC=local' -W \
  -b 'DC=corp,DC=example,DC=local' '(sAMAccountName=<test user>)' mail memberOf
```

The `memberOf` lines are the exact DNs for `groups`. Then from the image, as the container
sees DNS and the files (no Superset database is touched):

```bash
docker run --rm -v /etc/lauretta-superset/ldap:/app/ldap:ro --network <the stack's network> \
  -e PYTHONPATH=/app/docker/pythonpath_dev -v "$PWD/docker":/app/docker:ro \
  --entrypoint python <image> -m lauretta_ldap check <test user>
# ok   bind as CN=svc-superset,...
# ok   '<test user>' is CN=...
#      mail ...
#      roles ['Gamma', 'Public']
```

Run it for one user per group, the user in no group (expect `0 matches`), and the disabled one.

## 4. Before switching

- **Public must be empty.** Every AD user also gets `Public`. Secure mode keeps it empty
  (`PUBLIC_ROLE_LIKE = None`); confirm on the metadata database:
  `SELECT count(*) FROM ab_permission_view_role pvr JOIN ab_role r ON r.id = pvr.role_id WHERE r.name = 'Public';` → 0.
- **Existing users.** A Superset user whose username equals an AD login becomes that AD user
  (keeps their dashboards). To keep someone's dashboards, rename their Superset username to
  their `sAMAccountName` first. An existing user with the same email as a *different* AD user
  blocks that AD user's first login.
- **Local passwords stop working**, including `admin`'s: Superset uses one login method at a
  time. Make sure someone in the admin group can log in before relying on it. Keep the
  local `admin` password: it works again after a rollback.

## 5. Switch on, and back

The compose variable `SUPERSET_LDAP_DIR` (shell environment or `--env-file`, like
`SUPERSET_PG_CA`) names the directory; `docker-compose-ha.yml` mounts it read-only at
`/app/ldap`. Unset, it mounts the empty `docker/ldap-disabled/` and password login stays.

```bash
SUPERSET_LDAP_DIR=/etc/lauretta-superset/ldap docker compose -f docker-compose-ha.yml up -d
```

Every node at once, with the same directory contents. Otherwise some nodes take AD logins and
others local passwords.

**Rollback:** unset `SUPERSET_LDAP_DIR` (or remove `ldap.json`) and recreate the containers.

**Under raib-ha:** raib-ha writes the compose variables and runs compose; it does not pass
`SUPERSET_LDAP_DIR` yet. That needs a raib-ha change (the variable in its compose env file and
the directory on both nodes) before AD login can be switched on there.

## 6. Test

| Try | Expected |
|---|---|
| A user from each group | Logs in, with the group's roles plus Public |
| A domain user in no listed group | Refused |
| A disabled account | Refused |
| A member only through a nested group | Refused |
| A wrong password | Refused |
| `jdo*` with jdoe's password | Refused (`refused_input` in the audit trail) |
| Local `admin` | Refused |

Logins and failures are in the audit trail ([12](12-audit-trail.md)) with `method` `ldap`.
Failure reasons: `invalid_credentials` (wrong password, or not in a group, or disabled; AD does
not say which), `unknown_user`, `inactive_user`, `refused_input`. Repeated failures raise
`auth.repeated_failure` as for password logins.
`docker compose logs superset | grep -i ldap` shows connection and certificate errors.

## Behaviour to know

- **AD owns the roles.** Roles are recalculated from the groups at every login; a role added
  by hand in Superset disappears at that user's next login. Group DNs match case-insensitively.
- **Removing access:** take the user out of the group in AD (refused at the next login), or
  deactivate them in Superset (refused even while in the group). An existing session lasts
  until it expires (secure mode: 30 minutes idle).
- **Nested groups are ignored**, both for access and for roles.
- **The Register button is hidden**, since Superset has no self-registration under LDAP.
- **The JWT API login** (`/api/v1/security/login`) would accept `provider: ldap`; HA's nginx
  blocks that endpoint ([11](11-ha-mode.md)).
- A broken directory (bad JSON, unknown key, missing `ca.pem`, empty password, no python-ldap)
  stops Superset from starting, with the reason in the log, rather than quietly falling back to
  local passwords.
