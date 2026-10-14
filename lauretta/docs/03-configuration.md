# 03 Configuration

**For:** operators and developers changing how a stack behaves.

Two kinds of configuration, each layered: **environment files** (read by Compose) and
**Superset config modules** (Python, read by the app).

Anything that applies to every mode is written with `<compose-file>` and
`<env-file>` rather than one mode's filenames — substitute your row from the table
below.

## Environment files

Compose loads these in order; later files win.

| Mode | `<compose-file>` | Env files, in load order (`<env-file>` is the last one) |
|---|---|---|
| dev | `docker-compose.yml` | `docker/.env` → `docker/.env-local` |
| nondev | `docker-compose-non-dev.yml` | `docker/.env` → `docker/.env-local` |
| secure | `docker-compose-secure.yml` | `docker/.env` → `docker/.env-secure` |

- `docker/.env` — committed development defaults. **Never put a real secret or
  account detail here**; the file is tracked by git.
- `docker/.env-local` — gitignored, optional. Secrets and overrides for modes 1
  and 2: `SUPERSET_SECRET_KEY`, `ADMIN_PASSWORD`, SMTP, alternate ports. Template:
  `docker/.env-local.example`.
- `docker/.env-secure` — gitignored, **required** by mode 3. Generated from
  `docker/.env-secure.example` by `scripts/generate-secure-secrets.sh`.

Mode 3 deliberately does not load `.env-local`, so a developer's local overrides can
never weaken a production stack.

Env changes need a recreate, not a restart:
`docker compose -f <compose-file> up -d`.

Back up your `<env-file>` — see [07 Operations](07-operations.md).

## Superset config

Three layers, loaded in this order:

1. `docker/pythonpath_dev/superset_config.py` — committed, shared by all modes.
   Database/Redis/Celery wiring, feature flags, SMTP, and the Lauretta floor-map
   routes and hooks.
2. `docker/pythonpath_dev/superset_config_docker.py` — **does not exist unless you
   create it.** An upstream hook: layer 1 tries to import it at the end and silently
   carries on if it is missing. Create it by copying
   `docker/pythonpath_dev/superset_config_local.example` to override settings on
   your machine only. It is gitignored.
3. `docker/pythonpath_secure/superset_config_secure.py` — committed, hardened
   overrides. Loaded **last**, so a stale local override file cannot silently undo a
   security setting.

Layer 3 is gated on `SUPERSET_SECURE_MODE=true`, which only `docker/.env-secure`
sets. That file also extends `PYTHONPATH` with `/app/docker/pythonpath_secure` so
the import resolves. Modes 1 and 2 never take that branch.

To change a setting for **all** modes, edit layer 1. To change it for the secure
deployment only, edit layer 3.

### What the shared config turns on

Read `superset_config.py` for the exact values; this is the map.

| Setting | Effect |
|---|---|
| `ALERT_REPORTS`, `ALERT_REPORT_TABS` | Alerts and reports, including per-tab dashboard screenshots. See [05](05-alerts-and-reports.md). |
| `PLAYWRIGHT_REPORTS_AND_THUMBNAILS` | Screenshots via Playwright when it is installed. Mode 3 requires it. |
| `ENABLE_TEMPLATE_PROCESSING`, `ALLOW_ADHOC_SUBQUERY` | Jinja in datasets and charts. See the warning below. |
| `ENABLE_SUPERSET_META_DB` | The Superset meta database (`superset://`). See below. |
| `ALERT_REPORTS_NOTIFICATION_DRY_RUN = False` | Reports really send. |
| `CELERY_CONFIG.beat_schedule` | Report scheduler every minute, log pruning daily, and `lauretta.cleanup_temp_images` daily at 00:00. |
| `SMTP_*` | Read from the environment. See "Email (SMTP)". |
| `/api/v1/lauretta/*` routes, `Slice` hooks | Floor-map image serving and lifecycle. See [04](04-dashboards-and-floor-maps.md). |

**Jinja templating is enabled.** Jinja in datasets and charts runs with the
privileges of the Superset process, so anyone who can edit a chart can effectively
execute code. This is a roles question, not a config one: keep chart-edit and SQL
Lab permissions restricted to trusted users.

### The Superset meta database

`ENABLE_SUPERSET_META_DB` lets a single virtual dataset `JOIN` tables from different
database connections in one SQL query, which a dataset bound to one ordinary
connection cannot do. It exposes every configured connection's tables as virtual
tables under the `superset://` engine.

To use it, add a connection under **Settings → Database Connections → + Database**
and choose **Superset meta database**. Queries through it only see tables the
current user can already access.

## Email (SMTP)

Email is optional and off until it is configured. Everything it needs — host, port,
TLS flags, username, password, from-address — lives in **one commented-out block, in
one gitignored file**: your mode's `<env-file>`. Each `.example` template ships the
block already written out. Nothing SMTP-related is committed, so a fresh clone never
inherits someone else's mail account.

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

## Branding

Two settings, both commented out by default, in `docker/pythonpath_dev/superset_config.py`:

- `APP_ICON` — banner image in the nav bar
- `APP_NAME` — name in the browser tab

1. Put the banner image in `lauretta/branding/`. Compose mounts that directory at
   `/app/superset/static/assets/branding`.
2. Uncomment `APP_ICON` and set it to `/static/assets/branding/<your-banner>.png`.
3. Optionally uncomment and set `APP_NAME`.
4. Recreate the app: `docker compose -f <compose-file> up -d`.

`lauretta/branding/lauretta-banner.png` ships as an example.

## Other `docker/.env` values

- `SUPERSET_LOAD_EXAMPLES` has no effect: the examples step in `docker/docker-init.sh`
  is disabled and replaced by the dashboard import.
- `SUPERSET_ENV=development` shows a "development" badge in the nav bar. Set
  `SUPERSET_ENV=production` in your `<env-file>` to hide it; `docker/.env-secure`
  already does.
- `SERVER_WORKER_CLASS`, `SERVER_SOCKET_TIMEOUT`, `WORKER_MAX_REQUESTS*` — see
  "Gunicorn worker" in [07](07-operations.md).
