# 01 Overview

**For:** everyone. Start here.

## Superset in one page

Apache Superset is a web-based BI platform. The objects you work with, in the order
they depend on each other:

- **Database connection** — a SQLAlchemy URI to a data source (here, usually the
  Lauretta analytics Postgres). Passwords are stored encrypted with
  `SUPERSET_SECRET_KEY`.
- **Dataset** — a table or a SQL query (a *virtual* dataset) on one connection.
  Jinja templating is enabled, so datasets can read dashboard filter values.
- **Chart** — a visualisation of one dataset.
- **Dashboard** — a layout of charts plus native filters.
- **Alerts and reports** — scheduled screenshots or condition checks, delivered by
  email.

Superset keeps all of these in its own **metadata database** (Postgres, in the `db`
container). That database is separate from the databases your charts query.

Access is role-based (Flask-AppBuilder): `Admin`, `Alpha`, `Gamma` and custom roles.

## What the fork changes

The fork is upstream `6.1.0` plus a small set of commits. `base/6.1.0` tags the
upstream release commit, so this shows exactly what the fork carries:

```bash
git log --oneline base/6.1.0..HEAD
git diff --stat base/6.1.0 HEAD
```

At the time of writing: 96 files, of which 73 are new and 23 are edits to upstream
files.

| Area | What it adds | Read |
|---|---|---|
| Deployment | Three compose files (dev, nondev, secure), one image tag per mode, postgres 17 | [02](02-deployment-modes.md) |
| Secure mode | Hardened config layer, generated secrets, host nginx, non-root containers | [06](06-secure-mode.md) |
| Dashboards | `docker/import-dashboards.py` imports dashboard ZIPs listed in `config.json` on every start, injecting database credentials and generating floor-map charts | [04](04-dashboards-and-floor-maps.md) |
| Shared config | Alerts and reports, Playwright screenshots, Jinja, the Superset meta database, `/api/v1/lauretta/*` routes, floor-image lifecycle hooks | [03](03-configuration.md) |
| Charts | Floor Map and Customer Journey plugins; Bar chart data-zoom window | [10](10-development.md) |
| Operations | Timeout-aware gunicorn worker, Postgres major-version upgrade script | [07](07-operations.md) |

Upstream's example datasets are never loaded: the examples step in
`docker/docker-init.sh` is replaced by the Lauretta dashboard import.

## Repository layout

Only the fork's own files and the upstream files it edits are shown.

```
lauretta-superset/
├── lauretta/
│   ├── docs/                         ← these pages
│   ├── dashboards/
│   │   ├── config.example.json       ← template for config.json (git-ignored)
│   │   └── *.zip                     ← dashboard templates
│   ├── images/                       ← floor plan images; customs/ and tmp/ at runtime
│   └── branding/                     ← custom nav-bar banner
├── docker/
│   ├── docker-init.sh                ← (edited) runs import-dashboards.py
│   ├── import-dashboards.py          ← dashboard import and floor-map generation
│   ├── .env                          ← (edited) committed defaults, no secrets
│   ├── .env-local.example            ← template for modes 1 and 2 secrets
│   ├── .env-secure.example           ← template for mode 3 secrets
│   ├── pythonpath_dev/
│   │   ├── superset_config.py        ← (edited) shared config for all modes
│   │   └── lauretta_gunicorn.py      ← timeout-aware gunicorn worker
│   ├── pythonpath_secure/
│   │   └── superset_config_secure.py ← hardened overrides, mode 3 only
│   └── nginx/superset-secure.conf    ← host reverse proxy for mode 3
├── scripts/
│   ├── generate-secure-secrets.sh
│   └── upgrade-postgres.sh
├── superset-frontend/
│   ├── custom_plugin/                ← Floor Map and Customer Journey charts
│   └── plugins/plugin-chart-echarts/ ← (edited) Bar chart data-zoom window
├── docker-compose.yml                ← (edited) mode 1, dev
├── docker-compose-non-dev.yml        ← (edited) mode 2, nondev
├── docker-compose-secure.yml         ← mode 3, secure
└── Dockerfile                        ← (edited) adds the `production` stage
```

## License

This project extends Apache Superset, licensed under the Apache License 2.0. See
`LICENSE.txt`.
