# 11 HA mode (active-active)

**For:** anyone running Superset on two (or more) nodes behind DNS, sharing one
metadata database that fails over between hosts.

Mode 4. It layers on secure mode ([06](06-secure-mode.md)) the way secure mode layers on
the shared config: same image, same hardening, same `superset_config_secure.py`. Read 06
first; everything there about secrets, TLS and file ownership still applies.

| | |
|---|---|
| Compose file | `docker-compose-ha.yml` |
| Config | `docker/pythonpath_ha/superset_config_ha.py` (+ `lauretta_ha.py`) |
| nginx | `docker/nginx/superset-ha.conf` + `superset-ha-ready.js` (runs in the stack) |
| Container env | `docker/.env-ha.example` → `docker/.env-ha.local` (gitignored) or `SUPERSET_HA_ENV_FILE` |
| One-shot init | `docker/docker-init-ha.sh` |
| Web entrypoint | `docker/docker-ha-web.sh` |

## What is different from secure mode

- **No database container.** The metadata database is external: PostgreSQL with one
  writable server and hot standbys (e.g. streaming replication managed by a cluster
  manager). Every node connects with one multi-host libpq URL:
  - `host=<name A>,<name B>` with optional `hostaddr=<addr A>,<addr B>` (same order);
  - `target_session_attrs=read-write`: libpq keeps the first host that accepts writes;
  - `sslmode=verify-full` against the CA mounted at `/app/pgca/ca.pem`;
  - `connect_timeout=3`, TCP keepalives and `tcp_user_timeout`, so a vanished host
    fails in seconds;
  - engine `pool_pre_ping` and `pool_recycle=120`: after a failover each pooled
    connection is replaced on its next use. **No restart.**

  All of it is in the URL (`lauretta_ha.metadata_uri`), not in `connect_args`, because
  `superset db upgrade` builds its engine from the URL alone.
- **Sessions in the metadata database**, not in Redis, so a user can land on any node
  (Flask-Session's SQLAlchemy backend, table `ha_sessions`). Cookie flags, the signer and
  the 30-minute rolling timeout are those of secure mode. Logging out on one node logs
  out everywhere.
- **Filter state and explore form data** stay in the metastore (`key_value`), as
  Superset's defaults; the HA config pins them so a shared-config change cannot move them
  to Redis. Permalinks are in the metastore anyway.
- **Redis is a node-local cache**: results cache and rate-limit counters only, no
  persistence. Losing it costs a re-query.
- **No Celery**: no worker, no beat; alerts, reports, thumbnails and async queries are off.
- **Nothing runs at start.** Migrations, `superset init` and dashboard imports run once,
  through `docker/docker-init-ha.sh`, on one node. Two nodes doing them at once would
  race.
- **The web waits for a writable database** (`docker-ha-web.sh`). Superset cannot load
  without one, and gunicorn exits when a worker fails to boot. The wrapper waits, runs
  gunicorn, and waits again when gunicorn stops for that reason, so a cold start or a
  failover never crash-loops the container. A missing session table still exits loudly.
- **nginx is part of the stack** and terminates TLS. It adds `GET /ready` and an internal
  server for host-side checks (below).

The Lauretta extensions keep working unchanged: the HA mutator calls the shared
`FLASK_APP_MUTATOR` first (the `/api/v1/lauretta/*` routes, the floor-map hooks, permanent
sessions) and only then installs the session backend.

## Readiness: `/ready`

DNS hands out every node's address and may health-check each one. Point the check at
`https://<node address>:<port>/ready`, with SNI/Host set to the public name.

- `200 ready` only if `/run/superset-ha/ready.json` (the host directory
  `SUPERSET_READY_DIR`) says `"ready": true` and its `"at"` (Unix seconds) is under 30 s
  old. Anything else, including a missing or stale file, is `503 not ready`.
- nginx only reads that file, so DNS checks never touch Superset or the databases.
- **Something on the host must write it** every few seconds, atomically (temporary file
  + rename inside the directory; the directory is mounted, never the file). This repo
  does not ship that producer: what "ready" means depends on the platform (writable
  metadata reachable, local data sources current). raib-ha's producer is one example.
- `/health` stays the liveness check.

**Internal server** (`127.0.0.1:${SUPERSET_PROBE_PORT}` on the host, port 8082 in the
container; never published elsewhere):

| Path | |
|---|---|
| `/health` | Superset's liveness, without TLS |
| `/internal/ha/write-probe` | one disposable write (INSERT into `key_value`, always rolled back) through Superset's own engine and pool. `200 {"writable": true, "server": ...}` or `503`. No login. |
| `/ready` | same as the public one |

The public server answers `404` for everything under `/internal/` and blanks the
`X-Superset-Ha-Probe` header the probe requires, so the probe cannot be reached or forged
from outside.

## Setup

1. **Metadata database.** On the writable server: role and database (e.g. `superset`
   owning `superset`). pg_hba must accept that role **over TLS** from every node's
   address as the database sees it (the compose subnet `SUPERSET_SUBNET` for a server on
   the same host, the node's address for a remote one). Connections:
   nodes × `SERVER_WORKER_AMOUNT` × (`DATABASE_POOL_SIZE` + `DATABASE_MAX_OVERFLOW`) + a
   few; 2 × 4 × (5 + 5) = 80 fits PostgreSQL's default of 100.
2. **Image.** The secure-mode image (06); HA mode never builds or pulls it. Point
   `SUPERSET_IMAGE` at it (a tag or `sha256:<id>`), the same on every node.
3. **Env file.** `cp docker/.env-ha.example docker/.env-ha.local; chmod 600 docker/.env-ha.local`,
   fill it in. **Byte-identical on every node.**
4. **TLS.** `SUPERSET_TLS_DIR/{cert.pem,key.pem}`, the same on every node; `cert.pem`
   with the full chain. The certificate needs only the public DNS name.
5. **Compose variables** (shell environment or `--env-file`): at least `SUPERSET_IMAGE`,
   `SUPERSET_SUBNET`, `SUPERSET_TLS_DIR`, `SUPERSET_PG_CA`, `SUPERSET_READY_DIR`; see the
   header of `docker-compose-ha.yml` for all of them.
6. **One-shot init, on ONE node**, with no Superset web running anywhere:
   ```bash
   docker compose -f docker-compose-ha.yml run --rm --no-deps superset /app/docker/docker-init-ha.sh
   ```
   `db upgrade`, the admin user (only if missing), `superset init`, the session table, and
   with `HA_IMPORT_DASHBOARDS=yes` the dashboard import ([04](04-dashboards-and-floor-maps.md)).
   Idempotent.
7. **Start every node:** `docker compose -f docker-compose-ha.yml up -d`.

**Check:** on each node `curl -s http://127.0.0.1:${SUPERSET_PROBE_PORT}/internal/ha/write-probe`
prints `"writable":true` and the same `server` on every node; log in on one node, then
open the same URL on another with the same cookie (or through DNS): still logged in.

## Upgrades

Stop the web on **every** node, run the one-shot init once, then start them all.
Never run two Superset versions against one metadata database.

## Floor-map uploads (open issue)

The upload endpoint writes into `lauretta/images/` **on the node that served the
request**. Nothing copies it, so in active-active an image uploaded through node A is
missing on node B: the chart shows a broken image whenever B serves it, and deleting the
chart on B leaves A's file behind. Until this is solved, upload floor maps through one
node and copy `lauretta/images/` to the others by hand. Options:
- a shared volume (NFS or similar) for `lauretta/images`, at the cost of a new single
  point of failure;
- storing images in the metadata database (code change in the shared config);
- a sync job between nodes (rsync on change), accepting a short window.

## Known limits

- **Session writes.** Secure mode's rolling timeout (`SESSION_REFRESH_EACH_REQUEST`)
  updates the session row on every authenticated request, on the one writable server,
  across the network for every node but its own.
- **Anonymous requests** (health checks) cost no session write: `lauretta_ha` skips
  sessions that hold nothing (stock Flask-Session would issue a DELETE for each).
- **A vanished host costs time.** While the first host in `DATABASE_HOSTS` is
  unreachable, every *new* connection spends `connect_timeout` (3 s) on it first; the
  first request after it vanished can take up to ~20 s (keepalive + reconnect).
- **The metadata URI is logged in clear** by Superset's own boot check
  (`superset/initialization/__init__.py`, `check_and_warn_database_connection`) when the
  database is unreachable at boot. The wait in `docker-ha-web.sh` makes that rare; it
  does not make it impossible. Protect the container logs accordingly.
- `lauretta_ha` relies on two Flask-Session 0.8 internals (the session model's
  `query_class`, `save_session`); re-check both when Flask-Session is upgraded.
