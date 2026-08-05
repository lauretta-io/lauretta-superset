# Deployment modes

This repo supports three ways of running Superset. They share one image and one
Superset config; each mode layers on top of the last rather than forking it.

| Mode | Compose file | Use for | Access |
|---|---|---|---|
| 1. **dev** | `docker-compose.yml` | Local development, hot reload, Cypress | `http://localhost:8088` |
| 2. **nondev** | `docker-compose-non-dev.yml` | Locally hosted, plain HTTP, trusted network | `http://<host>:8088` |
| 3. **secure** | `docker-compose-secure.yml` | Internet-facing, must pass security scans | `https://<domain>` via host nginx |

> Modes 1 and 2 are unchanged from before and remain plain HTTP with development
> defaults. Do not expose either to an untrusted network.

---

## Mode 1 — dev

```bash
docker compose up
```

Frontend is served by a webpack dev server and rebuilds on change. Uses the `dev`
image stage, runs as `root`, loads examples.

## Mode 2 — nondev

```bash
docker compose -f docker-compose-non-dev.yml up -d
```

Gunicorn instead of the Flask dev server, frontend bundle baked into the image.
Still uses the `dev` image stage and runs as `root`.

Add `--build` the first time, and after any frontend change — this mode serves
`/static` from the image, so the bundle has to exist in it.

## Mode 3 — secure

```bash
# 1. Generate secrets (prompts for your public hostname if not given)
./scripts/generate-secure-secrets.sh superset.yourdomain.com

# 2. Uncomment and fill in the SMTP block, if you want alerts/reports by email
$EDITOR docker/.env-secure

# 3. Containers run as uid 1000; the upload directory must be writable by it.
#    lauretta/images/customs and lauretta/images/tmp are typically root-owned,
#    left behind by an earlier root-mode container. Uploads 500 without this.
sudo chown -R 1000:1000 lauretta/images
#    No sudo? The Docker daemon is already root:
#    docker run --rm -v "$PWD/lauretta/images:/x" alpine chown -R 1000:1000 /x

# 4. Build and start
docker compose -f docker-compose-secure.yml up -d --build

# 5. Install the reverse proxy on the host
sudo cp docker/nginx/superset-secure.conf /etc/nginx/sites-available/superset
sudo ln -s /etc/nginx/sites-available/superset /etc/nginx/sites-enabled/
$EDITOR /etc/nginx/sites-available/superset   # server_name + 3 cert paths
sudo certbot --nginx -d superset.yourdomain.com
sudo nginx -t && sudo systemctl reload nginx
```

Log in as `admin` with the password the generator printed, then change it in the UI.

### What mode 3 changes

**Image and runtime**
- Builds the `production` Dockerfile stage: based on `lean`, so it carries neither
  `requirements/development.txt` nor the `git`/`build-essential` toolchain. The
  postgres driver is baked in because `docker-bootstrap.sh` only installs it when
  running as root.
- Every container runs as the non-root `superset` user, with
  `no-new-privileges`, dropped capabilities, and pid/memory ceilings.
- Container logs are capped at 5 × 10 MB per service.
- Frontend is built with `SCARF_ANALYTICS=false`, removing the scarf.sh telemetry
  pixel. This has to be a build arg — webpack bakes it into the bundle.

**Network**
- Superset is published on `127.0.0.1:8088` only. TLS termination and the public
  listener are nginx's job on the host.
- Postgres and Redis publish nothing; they are reachable only on the compose network.
- Redis requires a password.

**If nginx runs on a different host**

The loopback bind assumes nginx is local. A remote proxy cannot reach `127.0.0.1`,
and the failure looks like a connect timeout rather than a refusal — nginx logs
`upstream timed out (110) while connecting to upstream` and answers 504. ("while
connecting" is the tell: the TCP handshake never completed. A wrong port with a live
host gives `connection refused` and a 502 instead.)

Widening the bind to the LAN address works but puts plaintext HTTP on the network,
which is the finding the loopback bind exists to avoid. Prefer a tunnel — WireGuard
or Tailscale between the two hosts — and bind to the tunnel address:

```yaml
ports:
  - "<tunnel-ip>:8088:8088"
```

Then point `proxy_pass` at `http://<tunnel-ip>:8088`. Traffic stays encrypted and
8088 never appears on the LAN. Two caveats: the address must exist before Docker
starts or the container fails with `cannot assign requested address`, and everything
on the tunnel can reach 8088 unless you add an ACL restricting it to the proxy.

**Application**
- `ENABLE_PROXY_FIX` so Superset knows the request arrived over HTTPS. Without it
  Talisman emits no HSTS header and Superset builds `http://` redirects.
- `Secure`/`HttpOnly`/`SameSite=Lax` session cookies, 30-minute rolling idle
  timeout, sessions stored server-side in Redis so they can be revoked.
- CSP without `unsafe-eval`, HSTS with a one-year max-age, `frame-ancestors 'none'`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`. Talisman and
  nginx both know how to set the last five, so nginx `proxy_hide_header`s the app's
  copy and then sets its own — otherwise each is emitted twice, which scanners flag
  (and the two `Permissions-Policy` values disagreed). CSP is left to the app: it is
  nonce-based and must be generated per response.
- The three `/api/v1/lauretta/*` routes require an authenticated session and return
  a JSON 401 otherwise. They are registered directly on the Flask app, so they never
  passed through FAB's permission layer; `POST .../images/upload` was an
  unauthenticated disk write. Uploads are also checked against image magic bytes,
  not just the filename suffix.
- Rate limiting enabled with Redis storage (per-worker in-memory counters would
  multiply the effective limit by the worker count).
- Bounded gunicorn request line and header sizes; `run-server.sh` defaults both to
  unlimited.
- Startup refuses to proceed on a placeholder `SUPERSET_SECRET_KEY` or
  `GUEST_TOKEN_SECRET`, a non-HTTPS `SUPERSET_PUBLIC_URL`, or an `admin`/`admin`
  password.
- Chromium runs **with** its sandbox. `WEBDRIVER_OPTION_ARGS` drops `--no-sandbox`
  and `--disable-setuid-sandbox`, which the shared config only carries because modes
  1 and 2 run the browser as root.

**Alerts and reports**
- Screenshots are taken with Playwright, not Selenium (`PLAYWRIGHT_REPORTS_AND_THUMBNAILS`).
  No image ships `chromedriver`; the Selenium path depends on Selenium Manager
  downloading one at runtime, which works as root and fails as uid 1000. Playwright's
  chromium is already in the image and is only read, never written.
- `WEBDRIVER_BASEURL` is the **public** URL, not `http://superset:8088`. Talisman's
  `force_https` answers plain HTTP with a 301 to `https://superset:8088`, where
  nothing terminates TLS, so the headless browser hangs until it times out.
  Consequence: **the worker needs public DNS and egress for `SUPERSET_PUBLIC_URL`** —
  report screenshots leave through the reverse proxy and come back.

---

## How the layering works

### Environment files

Compose loads these in order; later files win.

| Mode | `<compose-file>` | Env files, in load order (`<env-file>` is the last one) |
|---|---|---|
| dev | `docker-compose.yml` | `docker/.env` → `docker/.env-local` |
| nondev | `docker-compose-non-dev.yml` | `docker/.env` → `docker/.env-local` |
| secure | `docker-compose-secure.yml` | `docker/.env` → `docker/.env-secure` |

- `docker/.env` — committed development defaults. **Never put a real secret or
  account detail here**; the file is tracked by git.
- `docker/.env-local` — gitignored, optional. Secrets for modes 1 and 2. Template:
  `docker/.env-local.example`.
- `docker/.env-secure` — gitignored, **required** by mode 3. Generated from
  `docker/.env-secure.example` by `scripts/generate-secure-secrets.sh`.

Mode 3 deliberately does not load `.env-local`, so a developer's local overrides can
never weaken a production stack.

Anything that applies to every mode is written below with `<compose-file>` and
`<env-file>` rather than one mode's filenames — substitute your row from the table.

### Email (SMTP)

Email is optional and off until it is configured. Everything it needs — host, port,
TLS flags, username, password, from-address — lives in **one commented-out block, in
one gitignored file**. Uncomment it, fill it in, restart. Nothing SMTP-related is
committed, so a fresh clone never inherits someone else's mail account.

That file is your mode's `<env-file>`, and each one ships the block already written
out in its `.example` template.

```bash
cp docker/<env-file>.example docker/<env-file>   # if you don't have one yet
chmod 600 docker/<env-file>
$EDITOR docker/<env-file>                        # uncomment the SMTP block
docker compose -f <compose-file> up -d
```

For mode 3, `scripts/generate-secure-secrets.sh` writes `docker/.env-secure` from the
template for you, so start at the `$EDITOR` line.

Two things worth knowing, both from Superset itself:

- `SMTP_USER`/`SMTP_PASSWORD` are only needed if your relay requires authentication.
  `superset/utils/core.py` skips the SMTP login when either is empty, so an
  unauthenticated internal relay works with just `SMTP_HOST` and `SMTP_MAIL_FROM`.
- `SMTP_MAIL_FROM` must be a full address. Superset splits it on `@` when a report
  is sent (`superset/reports/notifications/email.py`), so a bare username fails at
  send time rather than at startup.

Check what the running stack resolved:

```bash
docker compose -f <compose-file> exec superset python -c \
  "from superset.app import create_app; c=create_app().config; \
   print(c['SMTP_HOST'], '|', c['SMTP_MAIL_FROM'], '|', bool(c['SMTP_PASSWORD']))"
```

### Image tags

Every image is tagged `lauretta-superset-<mode>:latest`, pinned explicitly in each
compose file. This matters more than it looks: all three build from the same
`Dockerfile` and the same directory, and an unpinned `image:` makes Compose derive
the tag from the project name plus the service name. Modes 1 and 2 use the same
project name and the same service names, so they collided on one tag — built with
*opposite* `DEV_MODE` values.

| Mode | Tag | Build target | `DEV_MODE` | Frontend |
|---|---|---|---|---|
| dev | `lauretta-superset-dev` | `dev` | `true` | webpack dev server on `:9000`, proxied by the compose nginx |
| nondev | `lauretta-superset-nondev` | `dev` | `false` | built into the image |
| secure | `lauretta-superset-secure` | `production` | `false` | built into the image, `SCARF_ANALYTICS=false` |

Two dev-only helper services build different stages and so keep their own tags:
`lauretta-superset-node` (the `superset-node` stage, webpack dev server) and
`lauretta-superset-websocket` (built from `./superset-websocket`).

The dev tag follows `SUPERSET_BUILD_TARGET`, so `SUPERSET_BUILD_TARGET=lean docker
compose up` produces `lauretta-superset-lean` rather than mislabelling a lean build
as `dev`.

All services within a mode share one tag, so each mode builds a single image instead
of four identical ones under per-service names.

`DEV_MODE=true` skips `npm ci` and `npm run build`, so the dev image ships an empty
`/app/superset/static/assets` — correct for mode 1, fatal for mode 2. When both
modes shared a tag, running `docker compose up` and then
`docker compose -f docker-compose-non-dev.yml up -d` silently reused the dev image
and every asset 404'd: the UI rendered as an unstyled page with a broken favicon,
while `/health` and the REST API kept returning 200. Mode 3 is isolated already by
its `name: superset-secure` project name.

To confirm an image really has a bundle:

```bash
docker run --rm --entrypoint sh <tag> -c 'ls /app/superset/static/assets | wc -l'
# dev: 0 (expected). nondev and secure: ~800.
```

### Superset config

Three layers, loaded in this order:

1. `docker/pythonpath_dev/superset_config.py` — committed, shared by all modes.
   Database/Redis/Celery wiring, feature flags, SMTP, and the Lauretta floor-map
   routes and hooks.
2. `docker/pythonpath_dev/superset_config_docker.py` — gitignored, optional local
   escape hatch.
3. `docker/pythonpath_secure/superset_config_secure.py` — committed, hardened
   overrides. Loaded **last**, so a stale local override file cannot silently undo a
   security setting.

Layer 3 is gated on `SUPERSET_SECURE_MODE=true`, which only `docker/.env-secure`
sets. That file also extends `PYTHONPATH` with `/app/docker/pythonpath_secure` so
the import resolves. Modes 1 and 2 never take that branch.

To change a setting for **all** modes, edit layer 1. To change it for the secure
deployment only, edit layer 3.

---

## Migrating between modes

Carrying dashboards, charts and saved database connections from a dev or nondev
stack into a secure one. The direction below is nondev → secure; the reverse works
the same way with the files swapped.

### Why the volumes are separate

Modes 1 and 2 declare no `name:`, so Compose derives the project from the directory
and prefixes their volumes `lauretta-superset_`. Mode 3 sets `name: superset-secure`
and gets `superset-secure_`. Same volume keys, different volumes:

```
lauretta-superset_db_home        superset-secure_db_home
lauretta-superset_superset_home  superset-secure_superset_home
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
  the same failure class as the upload directory in step 3 of the mode 3 setup.
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
    pg_dump -U superset -d superset > migrate.sql
docker compose -f docker-compose-non-dev.yml down

# 2. Restore into the secure stack. Let it keep its OWN generated DB password —
#    the volume is empty, so initdb applies the value from docker/.env-secure.
docker compose -f docker-compose-secure.yml up -d db
docker compose -f docker-compose-secure.yml exec -T db \
    psql -U superset -d superset < migrate.sql
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
`ValueError: Invalid decryption key`; see Operational notes for recovery.

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

## Verification

### The stack itself

```bash
# All three compose files parse
for f in docker-compose.yml docker-compose-non-dev.yml docker-compose-secure.yml; do
    docker compose -f "$f" config -q && echo "$f OK"
done

# The UI actually renders. /health and the REST API return 200 even when every
# static asset is missing, so check an asset explicitly rather than trusting them.
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8088/static/assets/images/favicon.png

# Secure config actually loaded. Read a value it sets rather than grepping the logs:
# superset_config.py logs "Loaded hardened secure configuration" through logger.info
# while the config is still being imported, which is before Superset configures
# logging — so the record is dropped and that grep never matches, loaded or not.
docker compose -f docker-compose-secure.yml exec superset python -c \
  "from superset.app import create_app; c = create_app().config; \
   print('secure config loaded:', c['ENABLE_PROXY_FIX'] and c['SESSION_COOKIE_SECURE'])"

# Non-root
docker compose -f docker-compose-secure.yml exec superset whoami        # superset
docker compose -f docker-compose-secure.yml exec superset id -u         # 1000

# App healthy
curl -sSf http://127.0.0.1:8088/health

# Celery reaches password-protected Redis. If REDIS_PASSWORD were missing from any
# of the cache/broker/session/ratelimit URLs, this is where it would surface.
docker compose -f docker-compose-secure.yml exec superset-worker \
    celery -A superset.tasks.celery_app:app inspect ping

# The floor-map routes now require a session. Expect 401 with a JSON body, not 200.
# X-Forwarded-Proto is needed or Talisman's force_https answers with a 301 first.
curl -s -H 'X-Forwarded-Proto: https' http://127.0.0.1:8088/api/v1/lauretta/floors
# -> {"error":"Authentication required"}
```

This is the one behaviour change that reaches modes 1 and 2 as well: those three
routes used to be anonymously reachable and now return 401. Any caller has to hold a
Superset session cookie.

### Through nginx, from an external host

```bash
curl -sSI https://superset.yourdomain.com/login/ | sort
```

Two things that make curl look like a bug when it isn't. Over HTTPS the session
cookie is `Secure`, so curl will not send it back to an `http://` URL — test the
authenticated flow against the real HTTPS origin, not `127.0.0.1:8088`. And
flask-wtf rejects an HTTPS POST with no `Referer` ("The referrer header is
missing"), which browsers always send but curl does not; add `-H "Referer: <origin>/"`.

Expect:
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy`, `Permissions-Policy`
- `Set-Cookie: ...; Secure; HttpOnly; SameSite=Lax`
- a `Content-Security-Policy` **without** `unsafe-eval` — this is the proof that
  `FLASK_DEBUG=false` took effect, since Superset swaps in `TALISMAN_DEV_CONFIG`
  (whose CSP allows `unsafe-inline` and `unsafe-eval`) whenever `app.debug` is true.

```bash
# Nothing but 80/443 should answer. Run this from a genuinely different host —
# from the server itself the loopback bind will look open.
nmap -p 80,443,8088,5432,6379 superset.yourdomain.com

docker run --rm -ti drwetter/testssl.sh https://superset.yourdomain.com
```

### Scanners

```bash
# Image CVEs. Compare the dev and production stages — the delta is the payoff for
# switching image bases.
docker build -q --target dev -t superset:scan-dev . && \
docker build -q --target production -t superset:scan-prod .
trivy image --severity HIGH,CRITICAL superset:scan-dev
trivy image --severity HIGH,CRITICAL superset:scan-prod

# Compose/container misconfiguration
trivy config docker-compose-secure.yml

# Secrets in the repo
docker run --rm -v "$PWD:/src" zricethezav/gitleaks:latest detect -s /src
```

---

## Operational notes

**Rebuild on a schedule.** Base-image and Python CVEs accumulate over time; a scan
that passes today will not pass in two months on an unchanged image. Rebuild
periodically and track [Superset security advisories](https://superset.apache.org/docs/security/)
for patch releases. Current version: 5.0.0.

**Never regenerate `SUPERSET_SECRET_KEY` on a live stack without re-encrypting.**
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

**Back up your `<env-file>`.** Both `docker/.env-local` and `docker/.env-secure` are
gitignored by design, so each exists in exactly one place. Lose the file and you lose
`SUPERSET_SECRET_KEY` with it — every saved database connection then has to be
re-entered by hand.

**Modes are mutually exclusive on one host.** The secure stack uses its own compose
project name (`superset-secure`) and container names, so it will not clobber a
running nondev stack — but they both want port 8088. Stop one before starting the
other. That project name also gives each mode its own volumes, so a secure stack
starts empty rather than picking up a dev stack's dashboards; see "Migrating between
modes" for moving data across.

**Jinja templating is enabled.** `ENABLE_TEMPLATE_PROCESSING` and
`ALLOW_ADHOC_SUBQUERY` are on in the shared config. Jinja in datasets and charts
runs with the privileges of the Superset process, so anyone who can edit a chart can
effectively execute code. This is a roles question, not a config one: keep chart-edit
and SQL Lab permissions restricted to trusted users.

### Known gaps

Not addressed by mode 3, in rough priority order:

- **Read-only root filesystems.** Gunicorn, celery beat's `/tmp/celerybeat.pid` and
  the Superset home cache all need writable paths, so `read_only: true` needs
  targeted `tmpfs` mounts and its own testing pass.
- **TLS on the internal Postgres connection.** Traffic stays on the compose bridge
  network, so this is defence in depth rather than a finding.
- **Secrets on the process environment.** `docker inspect` can read them. Moving to
  Docker or Vault secrets would address auditors who ask about this.
