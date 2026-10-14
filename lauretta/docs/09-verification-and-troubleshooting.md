# 09 Verification and troubleshooting

**For:** anyone checking that a stack works, or finding out why it doesn't.

## Verification

### The stack itself

```bash
# All three compose files parse
for f in docker-compose.yml docker-compose-non-dev.yml docker-compose-secure.yml; do
    docker compose -f "$f" config -q && echo "$f OK"
done

# The UI actually renders. /health and the REST API return 200 even when every
# static asset is missing, so check an asset explicitly rather than trusting them.
# Modes 2 and 3 only — in mode 1 this 404s by design, because the dev image has no
# bundle and the assets come from the webpack dev server instead:
#   curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:9000/static/assets/images/favicon.png
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

# Modes 2 and 3: the timeout worker is loaded (see "Gunicorn worker" in
# 07-operations.md), and closes a
# silent connection after SERVER_SOCKET_TIMEOUT (~15s) instead of hanging.
# Container is superset_app, or superset_app_secure in mode 3.
docker logs superset_app 2>&1 | grep "Using worker"   # lauretta_gunicorn.TimeoutThreadWorker
time bash -c 'exec 3<>/dev/tcp/127.0.0.1/8088; cat <&3'

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

## Troubleshooting

### Finding the logs

```bash
docker compose -f <compose-file> logs -f superset-init   # migrations and the dashboard import
docker compose -f <compose-file> logs -f superset        # the app
docker compose -f <compose-file> logs -f                 # everything
```

### The UI renders unstyled

The static bundle is missing. In mode 1, browse `:9000`, not `:8088`. In modes 2 and
3, rebuild with `--build`. See "Image tags" in [10 Development](10-development.md).

### Containers won't start

- `superset-init` failed and the app is waiting on it — read its logs first. A bad
  `path` in `config.json` is a common cause (see "Dashboard not importing").
- `ValueError: Invalid decryption key` — the `SUPERSET_SECRET_KEY` does not match the
  data. See "Rotating `SUPERSET_SECRET_KEY`" in [07](07-operations.md).
- `database files are incompatible with server` — the Postgres major version changed
  under an existing volume. See "Upgrading the Postgres major version" in [07](07-operations.md).
- `image ... already exists` or `pull access denied` on build — see "Which service
  builds" in [10](10-development.md).
- Mode 3 init exits complaining about `ADMIN_PASSWORD`, a placeholder secret or a
  non-HTTPS `SUPERSET_PUBLIC_URL` — the secure guards; fix `docker/.env-secure`.

Starting over with `down -v` deletes the metadata database — every dashboard, chart,
user and saved connection on that stack. Take a backup first ([07](07-operations.md)).

### Floor map uploads fail with 500 (mode 3)

`lauretta/images` is not writable by uid 1000. See step 1c of the
[mode 3 setup](06-secure-mode.md#setup).

### Floor-map routes return 401

Expected without a session; all three `/api/v1/lauretta/*` routes require login in
every mode. See [04](04-dashboards-and-floor-maps.md).

### Dashboard not importing

```bash
docker compose -f <compose-file> logs superset-init | \
  grep -E "Dashboard|TARGET DB UUID|⚠️|❌"
```

Messages printed by `docker/import-dashboards.py`:

| Message | Meaning / fix |
|---|---|
| `⚠️ Missing or invalid connections config for dashboard: <path>` | The entry has no `connections` object. Add one with `host`, `port`, `username`, `password`, `db`. |
| `⚠️ Could not read dashboard UUID from zip: <error>` | The ZIP is corrupt or not a Superset export. Re-export it. |
| `⚠️ No dashboard UUID found in zip — treating as new` | No dashboard YAML with a UUID. Re-export if unexpected; otherwise the import continues as a first-time setup. |
| `⚠️ Error checking dashboard existence: <stderr>` | The script could not query the metadata database. Check `superset-init` got through migrations. The import then treats the dashboard as new and, if it did exist, replaces it — UI layout edits are lost. See "What it does for each dashboard" in [04](04-dashboards-and-floor-maps.md). |
| `✅ Dashboard already exists in database — only updating database connection.` | Normal on every start after the first: only the connection is synced. Changes to `floors` and new template versions are not applied — see "The import pipeline" in [04](04-dashboards-and-floor-maps.md). |
| `ℹ️ No floors configured, skipping floor map generation` | Informational. Add `floors` if the dashboard needs floor maps. |
| `⚠️ No databases/ folder in extracted ZIP` / `⚠️ No datasets/ folder in extracted ZIP` | Incomplete export. Re-export from Superset. |
| `⚠️ No dashboard YAML found` / `⚠️ Could not resolve ROOT/GRID in dashboard layout` | The ZIP has no usable `dashboards/*.yaml` layout, so floor charts cannot be placed. Re-export. |
| `❌ Could not find or create extraction directory` | The ZIP could not be extracted. Check `path` exists and the file is a valid ZIP. |
| `⚠️ Cannot find UUID in ZIP: <zip>; using generated target UUID` | Usually harmless. |
| `❌ Error updating database via Python app-context: <stderr>` | The connection update failed. Check metadata DB health and migration state. |

A Python traceback ending in `FileNotFoundError` stops `superset-init`, so the app
never starts. Every `path` in `config.json` must name a ZIP that exists — the usual
culprit is the `{}` placeholder left over from `config.example.json`.

To rerun the import by hand with full output:

```bash
docker compose -f <compose-file> exec superset python /app/docker/import-dashboards.py
```

### Dashboard changes didn't apply

- **Edited `floors`, or replaced a template ZIP, and restarted.** The dashboard
  already exists, so only its connection was synced. See "What this means in
  practice" in [04](04-dashboards-and-floor-maps.md) for how to force a full import
  — a new template version needs its charts and datasets removed too, not just the
  dashboard.
- **Edited `config.json` and rebooted the host.** A reboot does not rerun
  `superset-init`. Run `docker compose -f <compose-file> up -d superset-init`.

### A dashboard shows another property's data

Two `config.json` entries use the same dashboard template on one instance. The
import does not support that; see "One template, one property per instance" in
[04](04-dashboards-and-floor-maps.md). Run one instance per property.

### A data connection fails

Test from the UI (**Settings → Database Connections → Edit → Test connection**), or
confirm what the import wrote:

```bash
docker compose -f <compose-file> logs superset-init | \
  grep -E "TARGET DB UUID|NEW URI|Updating existing database"
```

`NEW URI` is printed with the password redacted. The host is resolved from inside
the container — `localhost` there means the container itself, not the Docker host.

### Emails not arriving

See [05 Alerts and reports](05-alerts-and-reports.md): SMTP block in the
`<env-file>`, workers running, no dry-run message in the worker logs.
