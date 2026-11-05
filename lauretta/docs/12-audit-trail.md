# 12 Security audit trail

**For:** operators, and anyone answering an audit or security questionnaire.

Upstream Superset does not record logins, logouts, account changes, role changes or
data changes. Its `logs` table holds only page views and chart/API calls, and only
for endpoints that opt in. The fork adds an audit trail that records all of them, in
every mode (dev, nondev, secure, HA). The code is in
`docker/pythonpath_dev/lauretta_audit.py`.

## What is recorded

| Requirement | `action` values | Notes |
|---|---|---|
| User logon / logoff | `auth.login` (outcome `success` or `failure`), `auth.logout` | Form login and the JWT API login (`/api/v1/security/login`). A failure records the reason: `invalid_credentials`, `unknown_user` or `inactive_user`. |
| User account creation / deletion | `user.create`, `user.delete`, `user.update` | From the UI, the REST API and the CLI (`superset fab create-admin`, etc.). `user.update` covers deactivation, renames and password changes. A changed password is recorded as changed, never stored. |
| Role assignment / revocation | `role.assign`, `role.revoke` | One row per user, listing the roles added or removed. Recorded whichever side made the change: the user's roles, or `PUT /roles/<id>/users`. Group equivalents: `group.role_assign`, `group.role_revoke`, `user.group_add`, `user.group_remove`. |
| Permissions changed on a role | `role.permission_add`, `role.permission_remove` | Each row lists the permissions, e.g. `can_read on Dashboard`. Also `role.create`, `role.update`, `role.delete`. |
| Changes to application data | `data.create`, `data.update`, `data.delete` | Every table in the metadata database: dashboards, charts, datasets and their columns and metrics, database connections, saved queries, alerts and reports, RLS rules, tags, themes, and so on. Ownership and dashboard-membership changes are included. |
| | `data.sql_dml` | A SQL Lab statement that writes to a data source, whether it succeeded or was refused. |
| | `data.file_upload` | A floor-map image upload. |

Each row has:

| Column | Content |
|---|---|
| `event_time` | UTC time, to the microsecond |
| `category`, `action`, `outcome` | Type of activity and whether it succeeded |
| `actor_id`, `actor_username` | Who did it. `system` for CLI and init scripts, `anonymous` when nobody was logged in |
| `target_type`, `target_id`, `target_name` | What it was done to: table, primary key and a readable name |
| `details` | JSON. For creates and deletes, the row's values. For updates, `{"field": {"old": …, "new": …}}`. For role changes, the roles or permissions involved |
| `ip_address`, `user_agent`, `request` | Client address (behind the reverse proxy this is the real client, via `ENABLE_PROXY_FIX`), browser, and `METHOD /path` |
| `node` | Container hostname, which tells the HA nodes apart |
| `key_id`, `row_hmac` | Tamper evidence, see below |

**Never stored:** password hashes, database passwords (the password in `sqlalchemy_uri`
is masked), `encrypted_extra`, certificates, SSH keys, tokens, and any encrypted
column. These show up as `[REDACTED]`. Long values (dashboard layouts, chart
parameters) are cut at 1000 characters, with the full length and a SHA-256 of the
whole value, so a value can still be matched against a backup.

**Not recorded on purpose:** page views and queries (Superset's own `logs` and `query`
tables already hold those), SQL Lab tab state, filter state and permalinks
(`key_value`), favourites, server-side sessions (`ha_sessions`), and the permission
catalogue that `superset init` rebuilds (`ab_permission`, `ab_view_menu`,
`ab_permission_view`). Granting those permissions to a role *is* recorded.

### What "transactional data" means here

Superset does not hold business transactions. The data it owns is its metadata:
dashboards, charts, datasets, connections and the rest, and every change to it is
recorded as above. The source databases (people counts and so on) are read, not
written. Every connection the dashboard import creates has `allow_dml` and
`allow_file_upload` off. If an admin turns either on, that change is itself a
recorded `data.update` on `dbs`. Any SQL Lab write after that is a `data.sql_dml` row.

## Where it goes

1. **The `lauretta_audit_log` table** in the metadata database. Query it with any SQL
   client, for example:

   ```sql
   SELECT event_time, action, outcome, actor_username, ip_address, target_type,
          target_name, details
   FROM lauretta_audit_log
   WHERE event_time > now() - interval '7 days'
   ORDER BY id;
   ```

2. **stdout, one JSON line per event**, with `"lauretta_audit": 1` and the same fields
   (including the row `id`). Point a log shipper (Fluent Bit, Vector, the Docker
   `syslog`/`fluentd` logging driver, …) at the container logs and filter on that key
   to send the events to a SIEM. **This copy is only kept if something ships it:** the
   compose files use Docker's `json-file` driver with rotation (secure and HA: 5 × 10 MB
   per container), so unshipped lines are gone after a few days of normal traffic.

A data change and its audit row are written in the same database transaction. If the
change is rolled back, so is its audit row, and no stdout line is printed. If the
audit row cannot be written, the change fails. Logins and logouts are written in their
own transaction. A database failure there is logged as an error and the login still
goes ahead, with the stdout line printed as usual.

## Setup

Nothing to configure. The init creates the table, its triggers and its indexes, and
running it again changes nothing:

- modes 1–3: `docker/docker-init.sh` (the `superset-init` service, on every start);
- HA: `docker/docker-init-ha.sh` (the one-shot init). **Existing HA deployments must
  run the one-shot init once after upgrading to this version**, or the web refuses to
  start (`table(s) lauretta_audit_log missing`).

The table is created right after `superset db upgrade`, so the admin user and the
`superset init` role setup are the first rows. A web or worker that starts without the
table exits with an error rather than running unaudited.

The table is not part of Superset's Alembic migrations, for the same reason as the HA
session table: a fork migration would conflict on every upstream upgrade
([08](08-upgrading-upstream.md)).

## Tamper protection

**What the init sets up:**

- Triggers reject every `UPDATE`, `DELETE` and `TRUNCATE` on `lauretta_audit_log`,
  including from the application's own connection. Superset offers no screen or API
  that edits or deletes audit rows, so no Superset role, Admin included, can change
  them.
- Each row carries `row_hmac`, an HMAC-SHA256 over every field. The key is
  `LAURETTA_AUDIT_HMAC_KEY` if set, otherwise one derived from `SUPERSET_SECRET_KEY`.
  HA nodes share `SUPERSET_SECRET_KEY`, so they share the key. To check every row:

  ```bash
  docker compose -f <compose-file> exec superset python -m lauretta_audit verify
  # audit verify: 1834 ok, 0 TAMPERED, 0 signed with another key
  # audit verify: 2 ids absent (rolled back or deleted), first ranges: ['901-902']
  ```

  It exits 1 if any row fails. A changed row fails its HMAC. A deleted row leaves a
  gap in `id`. So does a change that was rolled back, so a gap on its own proves
  nothing. Look the missing ids up in the shipped stdout copy: if an id is there, it
  was committed and later deleted.

**What it does not stop on its own:** whoever owns the table, or a PostgreSQL superuser,
can disable the triggers. With the metadata credentials alone, nobody can produce a
valid HMAC. With the HMAC key (it is in the app's environment), someone can. The
shipped SIEM copy is the record that does not depend on this database. For real
separation, have a DBA move the table to a separate owner, so that the application's
user can only add rows:

```sql
CREATE ROLE lauretta_audit_owner NOLOGIN;
ALTER TABLE lauretta_audit_log OWNER TO lauretta_audit_owner;
ALTER FUNCTION lauretta_audit_log_append_only() OWNER TO lauretta_audit_owner;
GRANT SELECT, INSERT ON lauretta_audit_log TO superset;          -- the app's DB user
GRANT USAGE ON SEQUENCE lauretta_audit_log_id_seq TO superset;
```

The app's user must not be a superuser for this to mean anything. The `postgres`
image makes `POSTGRES_USER` one, which is the case in modes 1–3. The init leaves an
existing table alone, so it keeps working after the hand-over.

**Retention.** Nothing deletes audit rows. Archiving old rows is a DBA task, done as the
table owner with the triggers briefly disabled. At typical volumes (a few hundred
rows a day) the table stays small for years.

**Key rotation.** Rows keep the `key_id` they were signed with. After changing
`SUPERSET_SECRET_KEY` (see [07](07-operations.md)) or `LAURETTA_AUDIT_HMAC_KEY`, pass
the old value so earlier rows still verify:

```bash
docker compose -f <compose-file> exec -e LAURETTA_AUDIT_OLD_KEYS='<old key>' \
  superset python -m lauretta_audit verify
```

## Known gaps

- **Idle timeouts are not logoffs.** A session that expires after 30 minutes of
  inactivity has no logout row. The user's last recorded activity marks the end. A JWT
  API login has no logout either: the token simply expires.
- **Writes outside the application** are not seen: SQL run directly against the metadata
  database, or a data source written by other tools. Use the database's own auditing
  (e.g. `pgaudit`) for that.
- **Side effects written with raw SQL by Superset itself** are covered by their cause,
  not one by one. Deleting a dataset also removes its `datasource access` permission
  from every role, and its columns and metrics. The dataset deletion is recorded, with
  its values, but those follow-on rows are not.
- **`data.sql_dml` has no client address.** SQL Lab runs the statement on a worker
  thread. The user, time, database and SQL are recorded; `request` names the SQL Lab
  query.
- **Before the first `create-table`** (first install, during `superset db upgrade`)
  events go to stdout only.
