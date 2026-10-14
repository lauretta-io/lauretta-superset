# 07 Operations

**For:** operators keeping a stack running and its data safe.

Commands use `<compose-file>` and `<env-file>`; see the table in
[03 Configuration](03-configuration.md).

## Backups

Two things, and you need both:

- **Your `<env-file>`.** `docker/.env-local` and `docker/.env-secure` are gitignored
  by design, so each exists in exactly one place. Lose the file and you lose
  `SUPERSET_SECRET_KEY` with it — every saved database connection then has to be
  re-entered by hand.
- **The metadata database.** Dashboards, charts, users, saved connections:

  ```bash
  docker compose -f <compose-file> exec -T db \
      pg_dump -U superset -d superset > "$HOME/superset-$(date +%F).sql"
  ```

  Keep dumps outside the repo. They contain database-connection passwords,
  encrypted under `SUPERSET_SECRET_KEY` — a dump is only restorable with the key
  from the same `<env-file>`.

Also keep `lauretta/dashboards/config.json` and `lauretta/images/customs/`, which
are gitignored or runtime state.

## Rebuild on a schedule

Base-image and Python CVEs accumulate over time; a scan
that passes today will not pass in two months on an unchanged image. Rebuild
periodically and track [Superset security advisories](https://superset.apache.org/docs/security/)
for patch releases. The fork is based on Superset 6.1.0; moving to a newer release is
covered in [08 Upgrading upstream](08-upgrading-upstream.md).

## Rotating `SUPERSET_SECRET_KEY`

**Never regenerate it on a live stack without re-encrypting.**
It is the AES key for `dbs.password`, `dbs.encrypted_extra`, `dbs.server_cert`, the
`ssh_tunnels` credential columns, and `database_user_oauth2_tokens` — see
`EncryptedFieldFactory` in `superset/utils/encrypt.py`. It also signs session
cookies, so changing it logs everyone out.

This applies to all three modes — the key is read from whichever `<env-file>` your
mode loads. To rotate:

```bash
# db must be running for the dump; on a stack that has already failed to start,
# this brings it up without the app.
docker compose -f <compose-file> up -d db redis
docker compose -f <compose-file> exec -T db \
  pg_dump -U superset -d superset > superset-before-rotate.sql

# set the new SUPERSET_SECRET_KEY in docker/<env-file>, keeping a note of the old one
docker compose -f <compose-file> run --rm --no-deps superset \
  superset re-encrypt-secrets --previous_secret_key '<the old key>'
docker compose -f <compose-file> up -d
```

The same commands recover a stack already restarted onto the new key, where init dies
with `ValueError: Invalid decryption key`. The app container waits on init and so
never starts, which is why this is `run --rm --no-deps` rather than `exec`.
`re-encrypt-secrets` reads the columns with raw SQL
(`SecretsMigrator._select_columns_from_table`, `superset/utils/encrypt.py:138`), so
it never hits the ORM path that throws.

## Migrating between modes

Carrying dashboards, charts and saved database connections from a dev or nondev
stack into a secure one. The direction below is nondev → secure; the reverse works
the same way with the files swapped.

**Modes are mutually exclusive on one host.** The secure stack uses its own compose
project name (`lauretta-superset-secure`) and container names, so it will not clobber a
running nondev stack — but they both want port 8088. Stop one before starting the
other. That project name also gives each mode its own volumes, so a secure stack
starts empty rather than picking up a dev stack's dashboards; the steps below move data
across.

### Why the volumes are separate

Each compose file pins its own project name, and Compose prefixes volumes with it.
Modes 1 and 2 share `lauretta-superset`; mode 3 uses `lauretta-superset-secure`.
Same volume keys, different volumes:

```
lauretta-superset_db_home        lauretta-superset-secure_db_home
lauretta-superset_superset_home  lauretta-superset-secure_superset_home
```

That separation is deliberate — it is what stops a dev stack and a secure stack
started from this directory sharing state.

### Sharing the volumes instead is a trap

Pointing mode 3 at the mode 1/2 volumes with an explicit `name:` looks like the
short path. Three things make it a poor trade:

- **`POSTGRES_PASSWORD` is ignored on a non-empty data directory.** It is applied by
  `initdb`, which only runs on first start. Reusing an existing `db_home` keeps the
  *old* role password while `docker/.env-secure` presents a new one, so the app
  cannot authenticate against its own database.
- **Ownership is one-way.** Modes 1 and 2 run as root; mode 3 runs as uid 1000.
  Every file those modes wrote is root-owned and not writable by the secure stack —
  the same failure class as the upload directory in step 1c of the
  [mode 3 setup](06-secure-mode.md#setup).
- **`down -v` in either stack destroys both.**

Migrate the data instead. It keeps both stacks intact and is reversible.

### Steps

Modes are mutually exclusive: both want 8088, and two Postgres containers over one
data directory is corruption territory. Stop one before starting the other.

```bash
# 1. Dump from the source stack. `up -d db` brings up Postgres without the app, so
#    this also works on a stack that is failing to start.
docker compose -f docker-compose-non-dev.yml up -d db
docker compose -f docker-compose-non-dev.yml exec -T db \
    pg_dump -U superset -d superset > "$HOME/migrate.sql"   # outside the repo
docker compose -f docker-compose-non-dev.yml down

# 2. Restore into the secure stack. Let it keep its OWN generated DB password —
#    the volume is empty, so initdb applies the value from docker/.env-secure.
docker compose -f docker-compose-secure.yml up -d db
docker compose -f docker-compose-secure.yml exec -T db \
    psql -U superset -d superset < "$HOME/migrate.sql"
```

The dump carries `dbs.password`, `dbs.encrypted_extra`, `dbs.server_cert`, the
`ssh_tunnels` credential columns and `database_user_oauth2_tokens` still encrypted
under the **source** stack's `SUPERSET_SECRET_KEY`. Pick one of:

**Re-encrypt onto the secure key (preferred).** Leaves the generated key in
`docker/.env-secure` alone, so the deployment does not inherit a secret that has
been sitting in gitignored files on dev machines.

```bash
docker compose -f docker-compose-secure.yml run --rm --no-deps superset \
    superset re-encrypt-secrets --previous_secret_key '<source SUPERSET_SECRET_KEY>'
docker compose -f docker-compose-secure.yml up -d
```

**Or copy the key across.** Set `SUPERSET_SECRET_KEY` in `docker/.env-secure` to the
source value before first start. It must survive the mode 3 guards: at least 42
characters, and not one of the known development placeholders — a stack still on the
`TEST_NON_DEV_SECRET` in `docker/.env` cannot take this path and has to re-encrypt.

Either way, starting the app on a key that does not match the data fails init with
`ValueError: Invalid decryption key`; see "Rotating `SUPERSET_SECRET_KEY`" above for
recovery.

### Files outside the database

`lauretta/images` is a bind mount rather than a volume, so uploaded floor maps carry
over on their own — but the ownership rule still applies:

```bash
sudo chown -R 1000:1000 lauretta/images
```

Nothing in the Redis volume needs migrating: cache, Celery broker and (in mode 3)
server-side sessions. Let the secure stack build its own — it requires a password
the other modes do not set.

---

## Upgrading the Postgres major version

Needed whenever an upstream release moves the `image: postgres:N` pin and you have an
existing volume. A fresh stack needs none of this — `initdb` simply creates the
cluster at the new version.

`scripts/upgrade-postgres.sh` does the whole thing for any mode. It works out the
project, volume and container names from the compose file, and the old version from
the volume's own `PG_VERSION`:

```bash
./scripts/upgrade-postgres.sh nondev        # or: dev, secure
```

It stops the stack and backs up the raw volume as a tarball. Then it starts the old
cluster on its own and dumps every database with the *new* `pg_dump`: pg_dump reads
servers older than itself but not newer. It keeps the old cluster as a second volume,
`<project>_db_home_pg<old>_old`, and lets Compose create a fresh cluster at the new
version. Finally it restores the dumps and compares table counts before and after.
It leaves only `db` running. Superset's own migrations happen when you start the rest:

```bash
docker compose -f <compose-file> up -d
docker compose -f <compose-file> logs -f superset-init
```

Options: `--dump-dir DIR` (default `~/superset-pg-upgrade`) for the tarball and dumps,
`-y` to skip the confirmation. Keep the dump dir out of the repo. Both files contain
the metadata database, with `dbs.password`, `dbs.encrypted_extra` and the
`ssh_tunnels` columns encrypted but present. The script creates the directory as `0700`.

**Air-gapped hosts.** The script never pulls. It uses exactly two images,
`postgres:<old>` and `postgres:<new>`, and does its tar/cp work in the new one (it is
Debian-based), so no alpine or other helper image is involved. The old image is
normally still local from the stack it ran. Stage the new one beforehand:

```bash
docker save postgres:17 | gzip > postgres-17.tar.gz    # on a connected host
gunzip -c postgres-17.tar.gz | docker load              # on the air-gapped host
```

A missing image fails before anything is touched. Running the script again on an
already-upgraded volume is a no-op.

**Rolling back.** The old cluster is still on disk until you remove it:

```bash
./scripts/upgrade-postgres.sh <mode> --rollback
# then pin image: postgres:<old> back in the compose file before starting
```

Keep `<project>_db_home_pg<old>_old` until you have actually used the upgraded stack —
charts rendering, saved database connections still connecting. Remove it once you
are satisfied; it is a full second copy of the metadata.

**Do not carry a dump across modes.** Each mode has its own
`SUPERSET_SECRET_KEY`, and that key decrypts every stored database-connection
password. A dump restored into a mode with a different key leaves those columns
undecryptable, and it fails at query time rather than at restore — so it looks like
it worked. "Migrating between modes" above covers the supported route, via
`re-encrypt-secrets`.

## Gunicorn worker

Modes 2 and 3 run gunicorn with `lauretta_gunicorn.TimeoutThreadWorker`
(`docker/pythonpath_dev/lauretta_gunicorn.py`) instead of stock `gthread`. Mode 1
runs the Flask dev server and is unaffected.

Stock `gthread` never times out a connection that has not sent a complete request.
Port scanners leave exactly those behind, and they hang the app permanently in two
ways:

- **Silent connections** are parked in the worker's event loop forever. At
  `worker_connections` (1000) the worker stops accepting, healthchecks included.
- **Partial requests** (bytes but no line break, e.g. RDP probes) block a thread in
  `recv()` forever. With every thread held, nothing is served.

`--timeout` and `--keep-alive` cover neither case. The custom worker closes
connections that send nothing within `SERVER_SOCKET_TIMEOUT` seconds (default 15).
It also applies that timeout to each socket read and write, so a stalled client
frees its thread. Slow queries are unaffected: they wait on the database, not the
socket.

Wiring (no upstream script edited, no rebuild needed):

- `docker/.env` sets `SERVER_WORKER_CLASS`, which `run-server.sh` already passes to
  `--worker-class`. It also sets worker recycling (`WORKER_MAX_REQUESTS`) as a
  backstop; `docker/.env-secure` overrides those values.
- The module is on `PYTHONPATH` via the mounted `pythonpath_dev`, and is
  allow-listed in that directory's `.gitignore`.
- `docker/docker-healthcheck.sh` gives `curl` a `--max-time 10`. Without it, probes
  against a hung app never exit and pile up, one per interval.

Env changes need a recreate, not a restart:
`docker compose -f <compose-file> up -d --no-deps superset`.

The worker subclasses gunicorn internals, and its `accept()` is a copy of gunicorn
23.0.0's plus one line. When an upgrade bumps gunicorn, diff it against the new
`ThreadWorker.accept` and run the worker check in
[09 Verification](09-verification-and-troubleshooting.md).
