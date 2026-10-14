# Lauretta Superset documentation

Lauretta Superset is a fork of [Apache Superset](https://superset.apache.org/) 6.1.0
that adds packaged property-analytics dashboards, two custom chart types (Floor Map
and Customer Journey), an automated dashboard import at container start, and a
hardened, internet-facing deployment mode.

These pages are the single source of truth for everything the fork adds. Upstream
Superset behaviour is documented at <https://superset.apache.org/docs/>; it is only
repeated here where the fork changes it.

All Lauretta documentation lives under `lauretta/` so that it never conflicts with
upstream files when the fork is rebased onto a new release.

## Pages

| Page | Covers |
|---|---|
| [01 Overview](01-overview.md) | Superset concepts, what the fork changes, repository layout |
| [02 Deployment modes](02-deployment-modes.md) | dev / nondev / secure, quick start for each |
| [03 Configuration](03-configuration.md) | Env files, Superset config layers, feature flags, SMTP, branding |
| [04 Dashboards and floor maps](04-dashboards-and-floor-maps.md) | `config.json`, the import pipeline, floor images, the `/api/v1/lauretta` routes |
| [05 Alerts and reports](05-alerts-and-reports.md) | Enabling email delivery, scheduling from the UI |
| [06 Secure mode](06-secure-mode.md) | Setting up mode 3, what it hardens, reverse proxy, known gaps |
| [07 Operations](07-operations.md) | Migrating between modes, secret rotation, backups, Postgres upgrades, gunicorn worker |
| [08 Upgrading upstream](08-upgrading-upstream.md) | Moving the fork onto a newer Superset release |
| [09 Verification and troubleshooting](09-verification-and-troubleshooting.md) | Health checks, scanners, import log messages |
| [10 Development](10-development.md) | Custom plugins, upstream patches, image tags and builds |

## Reading paths

| I want to… | Read | Notes |
|---|---|---|
| Stand up a new deployment | 01 → 02 → 06 → 09 | For a secure (internet-facing) instance, read 06 in full before the first start. 03 for anything you need to change. |
| Move an existing dev/nondev stack to secure | 06 → 07 → 09 | Read "Migrating between modes" in 07 alongside 06's setup: the secret-key decision is made before the first start. |
| Run an existing deployment | 07 → 09 | Backups, secret rotation, Postgres upgrades, troubleshooting. Read 07 before the stack holds data you care about. |
| Onboard a property / manage dashboards | 01 → 04 → 05 | |
| Change the code | 01 → 10 → 03 | Read 08 before editing upstream files: every in-place edit is a future rebase conflict. |
| Upgrade to a new Superset release | 08 → 07 → 10 → 09 | 07 for the Postgres major-version step, 10 for the in-tree patches that will conflict, 09 to re-verify each mode. |
