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
| User logon / logoff | `auth.login` (outcome `success` or `failure`), `auth.logout` | Form login and the JWT API login (`/api/v1/security/login`). A failure records the reason: `invalid_credentials`, `unknown_user` or `inactive_user`. A browser login gets a session id (`details.session`) that its `auth.logout` carries too. The logout's `details.reason` is `logout` (the user logged out) or `idle_timeout` (see "Idle timeouts" below). A JWT login records when its tokens expire (`access_token_expires`, `refresh_token_expires`), because tokens have no logout. When Superset itself opens a browser session as a user to render a report, alert or thumbnail, that is an `auth.login` with `details.method` `machine` (no client address); it ends as an `idle_timeout` logout. |
| User account creation / deletion | `user.create`, `user.delete`, `user.update` | From the UI, the REST API and the CLI (`superset fab create-admin`, etc.). `user.update` covers deactivation, renames and password changes. A changed password is recorded as changed, never stored. |
| Role assignment / revocation | `role.assign`, `role.revoke` | One row per user, listing the roles added or removed. Recorded whichever side made the change: the user's roles, or `PUT /roles/<id>/users`. Group equivalents: `group.role_assign`, `group.role_revoke`, `user.group_add`, `user.group_remove`. |
| Permissions changed on a role | `role.permission_add`, `role.permission_remove` | Each row lists the permissions, e.g. `can_read on Dashboard`. Also `role.create`, `role.update`, `role.delete`. Deleting a dataset or database makes Superset delete its access permission from every role with raw SQL. Each role that held it gets a `role.permission_remove`, with `details.cause`. |
| Changes to application data | `data.create`, `data.update`, `data.delete` | Every table in the metadata database: dashboards, charts, datasets and their columns and metrics, database connections, saved queries, alerts and reports, RLS rules, tags, themes, and so on. Ownership and dashboard-membership changes are included. Superset saves dataset columns and metrics with SQLAlchemy's bulk methods, which skip the normal change tracking; those rows are recorded one by one too, but a newly added column or metric has no `target_id` (the bulk insert does not return it; `target_name` and `details.values.table_id` identify it). |
| | `data.sql_dml` | A SQL Lab statement that writes to a data source, whether it succeeded or was refused. A Jinja-templated query is recorded whenever it may have written, with `details.mutation` = `unknown: …`: Superset keeps only the template and the last statement it ran, not the rendered script. That is every templated query that failed, and every one on a database with `allow_dml` on. A templated query that succeeded on a database without DML cannot have written (Superset checks the rendered script) and is not recorded. |
| | `data.file_upload` | A floor-map image upload. |
| | `data.bulk_create`, `data.bulk_update`, `data.bulk_delete` (and `user.`/`role.`/`group.` for those tables) | One statement that changes many rows at once, such as `superset factory-reset`, a Superset command that deletes by filter, or the dashboard–chart links an asset import inserts. `details` has the number of rows and, for an insert, the rows (up to 1000); for an update or delete, the filter (`where`), the values set and the primary keys affected (up to 1000). |
| Repeated failed logins | `auth.repeated_failure` | One username, or one client address, failed to log in 5 times within 10 minutes (`LAURETTA_AUDIT_FAILED_LOGIN_THRESHOLD`, `LAURETTA_AUDIT_FAILED_LOGIN_WINDOW_SECONDS`). `target_type` says which (`username` or `ip_address`). An address alert lists the usernames tried, which shows password spraying. At most one alert per key per window, across all workers and HA nodes. |
| Activities of privileged accounts | `access.request` | Every request by a holder of a role in `LAURETTA_AUDIT_PRIVILEGED_ROLES` (default `Admin`): what they viewed, listed, queried, downloaded or exported. `details` has the method, path, query arguments (credential-like ones masked) and status. For SQL Lab, it also has the SQL text (including SELECTs). For chart data, it has the result format, so `csv`/`xlsx` shows a download. Changes they make also get their own `data.*`/`user.*`/`role.*` rows. Not recorded: static assets, health checks, and the frontend's own logging and polling (`QUIET_REQUESTS` in the code). |
| Access to the audit trail | `audit.report`, `audit.verify` | Somebody exported or verified the audit trail with the command-line tool, with the filters, the row count and the OS user. Reading the table or the files directly is not seen by the application, see "Known gaps". |

Each row has:

| Column | Content |
|---|---|
| `event_time` | UTC time, to the microsecond |
| `category`, `action`, `outcome` | Type of activity and whether it succeeded |
| `actor_id`, `actor_username` | Who did it. `system` for CLI and init scripts, `anonymous` when nobody was logged in |
| `target_type`, `target_id`, `target_name` | What it was done to: table, primary key and a readable name |
| `details` | JSON. For creates and deletes, the row's values. For updates, `{"field": {"old": …, "new": …}}`. For role changes, the roles or permissions involved |
| `ip_address`, `user_agent`, `request` | Client address (behind the reverse proxy this is the real client, via `ENABLE_PROXY_FIX`), browser, and `METHOD /path` |
| `node` | Which server wrote it: `LAURETTA_AUDIT_NODE` if set, else `DATABASE_APPLICATION_NAME` (HA mode; raib-ha sets `superset-ha-<node>`), else the container hostname (a container id, which changes on every recreate) |
| `key_id`, `row_hmac` | Tamper evidence, see below |

**Never stored:** password hashes, database passwords (the password in `sqlalchemy_uri`
is masked, and so is any query parameter named like a credential), `encrypted_extra`,
certificates, SSH keys, tokens, and any encrypted column. Inside JSON values (for example
a connection's `extra`), every key whose name contains `password`, `passwd`, `secret`,
`token`, `credential` or `private_key` is masked too. These show up as `[REDACTED]`. Long values (dashboard layouts, chart
parameters) are cut at 1000 characters, with the full length and a SHA-256 of the
whole value, so a value can still be matched against a backup.

**Not recorded on purpose:** page views and queries (Superset's own `logs` and `query`
tables already hold those), SQL Lab tab state, filter state and permalinks
(`key_value`), favourites, server-side sessions (`ha_sessions`), the audit trail's own
session tracking (`lauretta_audit_sessions`), and the permission
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

Three copies of every event:

1. **The `lauretta_audit_log` table** in the metadata database. Query it with any SQL
   client, for example:

   ```sql
   SELECT event_time, action, outcome, actor_username, ip_address, target_type,
          target_name, details
   FROM lauretta_audit_log
   WHERE event_time > now() - interval '7 days'
   ORDER BY id;
   ```

2. **The SIEM file**, one JSON line per event with `"lauretta_audit": 1` and the same
   fields (including the row `id`): `/app/superset_home/audit/audit.jsonl` inside the
   container (`LAURETTA_AUDIT_FILE`; set it empty to turn the file off). It holds audit
   events only, so the SIEM agent needs no filter. See "The SIEM file" below.
3. **stdout**, the same lines, mixed into the container log. The compose files rotate
   it (secure and HA: 5 × 10 MB per container), so it is gone after a few days. Use
   the file, not this, for the SIEM.

A data change and its audit row are written in the same database transaction. If the
change is rolled back, so is its audit row, and no stdout line is printed. If the
audit row cannot be written, the change fails. Logins and logouts are written in their
own transaction. A database failure there is logged as an error and the login still
goes ahead, with the stdout line printed as usual.

## The SIEM file

`/app/superset_home/audit/audit.jsonl` lives in the `superset_home` Docker volume of
each compose project. On the host it is:

```
/var/lib/docker/volumes/<project>_superset_home/_data/audit/audit.jsonl
```

To find it:

```bash
docker volume inspect <project>_superset_home -f '{{ .Mountpoint }}'
```

The project names:
- raib-ha: `raib-ha-superset-ha`, so
  `/var/lib/docker/volumes/raib-ha-superset-ha_superset_home/_data/audit/audit.jsonl`.
- this repo's HA compose file: `lauretta-superset-ha`.

How to watch it:
- **Every node has its own file**, holding the events that node handled. Point the
  SIEM agent at the file on each node. The `node` field tells them apart. Logins and
  requests land on whichever node served them, so neither file alone is complete.
- **Owner and access:** the file is owned by uid 1000 (the container's `superset`
  user), mode `0640`. The agent needs root, or read access to that path.
- **Writes:** each event is appended once its database transaction commits. Several
  workers append under a lock, so lines never interleave.
- **Rotation:** nothing rotates the file. Use the agent's own handling or `logrotate`
  (`create 0640 1000 1000`; `copytruncate` is not needed, because the app reopens the
  file for every batch).
- **Gaps:** the file and the table hold the same events with the same `id`s. An `id`
  in the SIEM but missing from the table means a deleted row. The SIEM copy is the
  one that settles it.
- **Protection:** this file is not protected on the host (root can edit it). The
  protected copy is the one in the SIEM, which is why it has to be shipped.

## Reports

```bash
docker exec <superset container> python -m lauretta_audit report \
    --from 2026-11-01 --to 2026-11-30 [--user NAME] [--category auth] \
    [--action auth.login] [--out /app/superset_home/audit/report.csv]
```

A CSV with every column, oldest first, plus `seal_ok`: `yes` if the row's HMAC checks
out, `NO` if the row was altered or was signed with a key not known here (see "Key
rotation"). Dates are UTC and `--to` is inclusive. Without `--out` the CSV goes to
stdout. A file is written `0600`. Each run is recorded as an `audit.report` event.
`verify` is recorded the same way, as `audit.verify`.

Categories: `auth`, `user`, `role`, `group`, `data`, `access`, `audit`.

## Checklist 18.6 (Audit Trails and Report)

| Item | Covered by |
|---|---|
| 18.6.2.1 Login/logout | `auth.login`, `auth.logout` (incl. idle timeouts) |
| 18.6.2.2 Privileged/administrator activities | `access.request` for every request, plus the change rows |
| 18.6.2.3 Failed login | `auth.login` outcome `failure`, with the reason |
| 18.6.2.4 Data creation/modification/deletion | `data.*`, `data.sql_dml`, bulk rows |
| 18.6.2.5 Sensitive master data | changes to `dbs`, `ab_user`, `ab_role`, RLS rules etc., secrets redacted |
| 18.6.2.6 Repeated failed logins | `auth.repeated_failure` |
| 18.6.2.7 Accounts, role assignment and changes | `user.*`, `role.assign/revoke`, `role.permission_*` |
| 18.6.2.8 Access to the audit log | `audit.report` / `audit.verify` for access through this tool. Direct database reads need pgaudit, and reads of the files need host auditing (auditd); both are outside this repository (raib-ha). |
| 18.6.2 Reports | `python -m lauretta_audit report` |
| 18.6.3 Protected from modification/deletion | "Tamper protection" below, plus the SIEM copy |

## Setup

Nothing to configure. The init creates the two tables (`lauretta_audit_log` with its
triggers and indexes, and `lauretta_audit_sessions`, see "Idle timeouts"). Running it
again changes nothing:

- modes 1–3: `docker/docker-init.sh` (the `superset-init` service, on every start);
- HA: `docker/docker-init-ha.sh` (the one-shot init). **Existing HA deployments must
  run the one-shot init once when upgrading to this version**, with the web stopped on
  every node *before* the new code reaches any node, or the web refuses to start
  (`table(s) lauretta_audit_log missing`, or `lauretta_audit_sessions` on a node that
  already had the first). See "Upgrades" in [11](11-ha-mode.md).

The table is created right after `superset db upgrade`, so the admin user and the
`superset init` role setup are the first rows. A web or worker that starts without the
table exits with an error rather than running unaudited.

The table is not part of Superset's Alembic migrations, for the same reason as the HA
session table: a fork migration would conflict on every upstream upgrade
([08](08-upgrading-upstream.md)).

## Idle timeouts

Users are logged out after 30 minutes without a request (`PERMANENT_SESSION_LIFETIME`,
[03](03-configuration.md)). Nobody performs that logout, so the audit trail keeps track
of it itself:

- A browser login adds a row to `lauretta_audit_sessions`. Requests keep its
  `last_seen` current to within `LAURETTA_AUDIT_TOUCH_SECONDS` (default 60). That's
  one small write per active user per minute.
- Each worker sweeps the table every `LAURETTA_AUDIT_SWEEP_SECONDS` (default 60),
  during an ordinary request. A session idle for longer than the lifetime + touch
  interval + 30 seconds is closed with an `auth.logout`, `details.reason` `idle_timeout`.
  Its `event_time` is when the session expired (`last_seen` + lifetime), so it is
  accurate to within the touch interval. The row's `id` follows the order the sweep
  found it in, so sort by `event_time`, not `id`, for a timeline.
- A sweep only runs while somebody uses Superset. A timeout during a quiet night is
  recorded with the first request in the morning, still with the right `event_time`.
  To record it sooner, run `python -m lauretta_audit sweep-sessions` from cron.
- Several workers and HA nodes sweep the same table. Each session is closed by
  exactly one of them.
- The two settings can be overridden in the Superset config (e.g.
  `superset_config_docker.py`). There is no reason to change them in production.

`lauretta_audit_sessions` is working state, not audit trail: it is updated in place
and has no triggers. The audit record is the `auth.logout` row.

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

  After a failover (a standby promoted) the ids jump by about 32: PostgreSQL logs
  sequence values ahead, and the new primary continues after them. Such a gap at a
  known failover time is normal.

  It exits 1 if any row fails: a changed row, or a row whose `key_id` matches none of
  the keys it knows (pass earlier keys as shown under "Key rotation"; an unknown key is
  also what an edited `key_id` looks like). A changed row fails its HMAC. A deleted row leaves a
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

- **JWT API logins have no logout.** A token cannot be ended, it expires. The login
  row records when (`access_token_expires`, and `refresh_token_expires` when a refresh
  token was issued).
- **Writes outside the application** are not seen: SQL run directly against the
  metadata database, or a data source written by other tools. That needs the
  database's own auditing (e.g. `pgaudit` on the metadata server), which is outside
  this repository.
- **Other raw-SQL side effects in Superset** are covered by the change that caused
  them, not one by one. Renaming a dataset or database also renames its permission,
  and roles keep holding it under the new name. The rename itself is recorded.
  (Permission *removals* are recorded per role, see the table above.)
- **`data.sql_dml` for an async SQL Lab query** (run by a Celery worker) has no client
  address, only the user, time, database and SQL. Synchronous queries, the only kind
  on Lauretta connections (`allow_run_async` off), record the client that submitted
  them.
- **Before the first `create-table`** (first install, during `superset db upgrade`)
  events go to stdout and the SIEM file only, without an `id`.
- **Reading the audit trail outside this tool** is not recorded by the application.
  That covers a SQL client on the metadata database and reading the SIEM file or
  container logs on the host. This needs pgaudit on `lauretta_audit_log` and auditd on
  the file and on `docker exec`, both set up by raib-ha. `audit.report` records the
  container's OS user (`superset`), not the person who ran `docker exec`; the host
  audit log has that.
- **`access.request` is per request, not per click.** Opening a dashboard as an
  administrator records each API call it makes (one per chart).
