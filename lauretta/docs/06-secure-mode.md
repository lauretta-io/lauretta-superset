# 06 Secure mode

**For:** operators running an internet-facing deployment.

Mode 3 (`docker-compose-secure.yml`) is the only mode fit for an untrusted network.
It builds a separate image, runs every container as a non-root user, loads a
hardened config layer on top of the shared one, and expects TLS to be terminated by
nginx on the host.

## Setup

Work through the steps in order. **Everything in step 1 happens before the first
start.** Some settings are applied only once, when the stack first initialises its
database; changing them afterwards either does nothing or breaks the stack.

### Prerequisites

- Docker with the Compose plugin.
- A DNS name for the instance pointing at this host.
- nginx and certbot installed on the host. If nginx runs on a different machine, read
  "If nginx runs on a different host" below first: it changes a setting in step 1.
- Access details for the property's analytics database.

### Step 1 — Configure

#### 1a. Generate `docker/.env-secure`

```bash
./scripts/generate-secure-secrets.sh superset.yourdomain.com   # prompts if omitted
```

This writes `docker/.env-secure` (mode 600) from `docker/.env-secure.example`, with
every secret generated. It refuses to overwrite an existing file. Open the file and
review it against the table below before going further:

```bash
$EDITOR docker/.env-secure
```

| Setting | Set by the script | Change it… | Notes |
|---|---|---|---|
| `SUPERSET_PUBLIC_URL` | `https://<your hostname>` | any time (recreate) | Must be `https://`. Used for report links and screenshots. |
| `SUPERSET_SECRET_KEY` | random | **before first start only** | Encrypts stored connection passwords. To bring dashboards over from a dev/nondev stack, decide now how to handle it — see "Migrating between modes" in [07](07-operations.md). Changing it later needs a re-encrypt. |
| `DATABASE_PASSWORD` / `POSTGRES_PASSWORD` | random, identical | **before first start only** | Postgres applies `POSTGRES_PASSWORD` only when it initialises an empty volume. The two must match. |
| `ADMIN_PASSWORD` | random, 24 chars | **before first start only** | Used only to create the `admin` user. See "Step 4 — First login". |
| `REDIS_PASSWORD`, `GUEST_TOKEN_SECRET`, `EXAMPLES_PASSWORD` | random | rarely | No need to touch. |
| SMTP block | commented out | any time (recreate) | Optional. Uncomment to send alerts and reports — see [05](05-alerts-and-reports.md). |
| `SERVER_WORKER_AMOUNT`, `SERVER_THREADS_AMOUNT`, `CELERYD_CONCURRENCY` | template defaults (4, 20, 4) | any time (recreate) | Optional. Size to the host; roughly (2 × cores) + 1 gunicorn workers. |
| Mode switches (`SUPERSET_SECURE_MODE`, `FLASK_DEBUG`, `DEV_MODE`, `SUPERSET_ENV`, `PYTHONPATH`) | template values | never | These are what turn the hardening on. Leave them exactly as they are. |

**Back up `docker/.env-secure` now**, somewhere outside the repo. It is the only copy
of `SUPERSET_SECRET_KEY` — see "Backups" in [07](07-operations.md).

#### 1b. Dashboards and floor images

```bash
cp lauretta/dashboards/config.example.json lauretta/dashboards/config.json
chmod 600 lauretta/dashboards/config.json
$EDITOR lauretta/dashboards/config.json
cp <floor plan images> lauretta/images/
```

Without `config.json` the stack starts with no dashboards. Replace the `{}` in the
template's `path`: a path that does not exist stops `superset-init`, and the app
never starts. Field reference: [04 Dashboards and floor maps](04-dashboards-and-floor-maps.md).

Floor-map charts are generated only when a dashboard is first imported, so get
`floors` right before the first start.

#### 1c. File ownership

Containers run as uid 1000, and the floor-map upload endpoint writes into
`lauretta/images`. `lauretta/images/customs` and `tmp` are typically root-owned,
left behind by an earlier dev or nondev container; uploads fail with 500 until this
is fixed.

```bash
sudo chown -R 1000:1000 lauretta/images
# No sudo? The Docker daemon is already root:
# docker run --rm -v "$PWD/lauretta/images:/x" alpine chown -R 1000:1000 /x
```

#### 1d. Optional

- **Branding** — banner and app name, set in `superset_config.py`. See "Branding"
  in [03](03-configuration.md).
- **Remote reverse proxy** — if nginx is not on this host, change the `ports:` bind
  in `docker-compose-secure.yml` as described in "If nginx runs on a different
  host" below.

### Step 2 — First start

```bash
docker compose -f docker-compose-secure.yml up -d --build
docker compose -f docker-compose-secure.yml logs -f superset-init
```

`superset-init` runs migrations, creates the `admin` user, imports the dashboards
and exits; the app starts after it. It refuses to run on a placeholder
`SUPERSET_SECRET_KEY` or `GUEST_TOKEN_SECRET`, a non-HTTPS `SUPERSET_PUBLIC_URL`, or
an `ADMIN_PASSWORD` that is unset or `admin`.

The app listens on `127.0.0.1:8088` only, so it is not reachable from outside until
the next step.

### Step 3 — Reverse proxy

```bash
sudo cp docker/nginx/superset-secure.conf /etc/nginx/sites-available/superset
sudo ln -s /etc/nginx/sites-available/superset /etc/nginx/sites-enabled/
$EDITOR /etc/nginx/sites-available/superset   # server_name (twice) + the 3 certificate paths
sudo certbot --nginx -d superset.yourdomain.com
sudo nginx -t && sudo systemctl reload nginx
```

Then run the checks in [09 Verification](09-verification-and-troubleshooting.md).

### Step 4 — First login

`generate-secure-secrets.sh` printed the admin password when it finished:

```
  Admin login:    admin / <generated password>
```

It is also the `ADMIN_PASSWORD` line in `docker/.env-secure`. Log in at
`https://superset.yourdomain.com` as `admin` with it.

`ADMIN_PASSWORD` is only used when the `admin` user is created, on the first start
against an empty database. After that, `superset fab create-admin` finds the
existing user and leaves it alone. So:

- **To change the password, log in as `admin` and use the UI:** user menu →
  **Info** → **Reset my password**. Editing `docker/.env-secure` and restarting does
  not change it.
- Once you have changed it in the UI, the value in `docker/.env-secure` is stale.
  Leave it there — init still checks it is not `admin` — but don't treat it as the
  current password.
- The password also appears in plain text in the `superset-init` log from the first
  start (`Setting up admin user ( admin / … )`), which is one more reason to change
  it after the first login.

## What mode 3 changes

### Image and runtime

- Builds the `production` Dockerfile stage: based on `lean`, so it carries neither
  `requirements/development.txt` nor the `git`/`build-essential` toolchain. The
  postgres driver is baked in because `docker-bootstrap.sh` only installs it when
  running as root.
- Every container runs as the non-root `superset` user, with
  `no-new-privileges`, dropped capabilities, and pid/memory ceilings.
- Container logs are capped at 5 × 10 MB per service.
- Frontend is built with `SCARF_ANALYTICS=false`, removing the scarf.sh telemetry
  pixel. This has to be a build arg — webpack bakes it into the bundle.

### Network

- Superset is published on `127.0.0.1:8088` only. TLS termination and the public
  listener are nginx's job on the host.
- Postgres and Redis publish nothing; they are reachable only on the compose network.
- Redis requires a password.

### Application

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

### Alerts and reports

- Screenshots are taken with Playwright, not Selenium (`PLAYWRIGHT_REPORTS_AND_THUMBNAILS`).
  No image ships `chromedriver`; the Selenium path depends on Selenium Manager
  downloading one at runtime, which works as root and fails as uid 1000. Playwright's
  chromium is already in the image and is only read, never written.
- `WEBDRIVER_BASEURL` is the **public** URL, not `http://superset:8088`. Talisman's
  `force_https` answers plain HTTP with a 301 to `https://superset:8088`, where
  nothing terminates TLS, so the headless browser hangs until it times out.
  Consequence: **the worker needs public DNS and egress for `SUPERSET_PUBLIC_URL`** —
  report screenshots leave through the reverse proxy and come back.

## If nginx runs on a different host

The loopback bind on `127.0.0.1:8088` assumes nginx is local. A remote proxy cannot reach `127.0.0.1`,
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

## Known gaps

Not addressed by mode 3, in rough priority order:

- **Read-only root filesystems.** Gunicorn, celery beat's `/tmp/celerybeat.pid` and
  the Superset home cache all need writable paths, so `read_only: true` needs
  targeted `tmpfs` mounts and its own testing pass.
- **TLS on the internal Postgres connection.** Traffic stays on the compose bridge
  network, so this is defence in depth rather than a finding.
- **Secrets on the process environment.** `docker inspect` can read them. Moving to
  Docker or Vault secrets would address auditors who ask about this.
