# 04 Dashboards and floor maps

**For:** whoever onboards a property or maintains its dashboards.

Standard Superset expects you to import dashboards and enter database passwords by
hand in the UI. Here, every dashboard listed in `lauretta/dashboards/config.json` is
imported automatically the first time the stack starts, with its database connection
filled in and, optionally, one Floor Map chart generated per floor. Later starts keep
the connections in sync with `config.json` but leave existing dashboards alone — see
"The import pipeline".

## config.json

`lauretta/dashboards/config.json` is git-ignored — it holds database credentials.
Start from the committed template:

```bash
cp lauretta/dashboards/config.example.json lauretta/dashboards/config.json
chmod 600 lauretta/dashboards/config.json
```

```json
{
  "dashboards": [
    {
      "path": "/lauretta/dashboards/property_dashboard_template_v6.zip",
      "connections": {
        "database_display_name": "my_database",
        "host": "db.example.internal",
        "port": 5432,
        "username": "your_username",
        "password": "your_password",
        "db": "your_database_name",
        "timezone": "Asia/Singapore"
      },
      "floors": [
        { "id": 1, "name": "Ground", "image": "floor_plan_GF.jpeg" },
        { "id": 2, "name": "Level 1", "image": "floor_plan_L1.jpeg" }
      ]
    }
  ]
}
```

> The template ships `property_dashboard_template_v{}.zip`. The `{}` is a literal
> placeholder, not a pattern: replace it with the version of a ZIP that exists in
> `lauretta/dashboards/`. A path that does not exist stops `superset-init` with a
> `FileNotFoundError`, and the app never starts.

Shipped templates: `property_dashboard_template_v6.zip`, `live_metrics_template_v1.zip`.

**Fields**

- `dashboards` — one entry per dashboard to import. Only listed dashboards are imported.
  - `path` (required) — the dashboard ZIP. A leading `/lauretta/` is resolved to
    `/app/lauretta/` inside the container.
  - `connections` (required) — the database the dashboard's datasets query.
    - `database_display_name` — the connection's name in the Superset UI.
    - `host`, `port`, `username`, `password`, `db` (required).
    - `timezone` (optional) — IANA name (`"UTC"`, `"Asia/Singapore"`). Sets the
      Postgres session timezone through
      `extra.engine_params.connect_args.options = -c timezone=<tz>`.
  - `floors` (optional) — generates Floor Map charts. See "Floor maps" below.
    - `id` (required) — **the floor's ID as stored in your database**, not a display
      order. It must match the `floor_id` values in the data source; numbering
      floors `1, 2, 3…` by position shows the wrong map whenever the database uses
      different IDs.
    - `name` (required) — display name (`"Ground"`, `"Level 1"`).
    - `image` (required) — usually a filename in `lauretta/images/`. Values starting
      `/api/v1/lauretta/images/…`, an absolute `/…` or `http(s)://…` are also
      accepted.

### Connection identity

Each dashboard's database connection gets a stable UUID derived from its `path`
alone. So:

- Changing `host`, `port`, `username`, `password`, `db` or `database_display_name`
  and restarting updates the **same** connection.
- Changing `path` produces a new UUID and therefore a **new** connection record. To
  point a second dashboard at a different database, give it its own entry.

## The import pipeline

### When it runs

`docker/docker-init.sh` runs `docker/import-dashboards.py` inside `superset-init`,
after migrations, admin creation and `superset init`.

`superset-init` runs on **every** `docker compose -f <compose-file> up`, not only the
first, and the app waits for it to finish. It does **not** run when:

- the host reboots — Docker restarts the app, worker and beat
  (`restart: unless-stopped`), but `superset-init` has no restart policy;
- only the app is restarted (`docker compose -f <compose-file> restart superset`).

To run it on demand:

```bash
docker compose -f <compose-file> up -d superset-init
docker compose -f <compose-file> logs -f superset-init
```

Or directly, against a running stack:

```bash
docker compose -f <compose-file> exec superset python /app/docker/import-dashboards.py
```

### What it does for each dashboard

For each entry in `config.json`, the script derives the connection UUID from `path`,
reads the **dashboard's** UUID from the ZIP, and looks that dashboard up in the
metadata database. That lookup alone decides what happens. Whether the database
connection already exists does not.

| Dashboard UUID already in the metadata DB? | What happens |
|---|---|
| **No** — first start, empty metadata DB, or the dashboard was deleted | **Full import.** Extract the ZIP; if `floors` is set, generate the Floor Map dataset and charts and add them to the layout; write the real credentials into `databases/*.yaml` and the connection UUID into every `datasets/*.yaml`; re-zip to a temporary `<name>.imported.zip`; run `superset import-dashboards`; delete the temporary files. Your original ZIP is never modified. Any connections, datasets and charts in the ZIP that already exist, matched by UUID — for example left behind after the dashboard was deleted — are kept exactly as they are; Superset's importer never overwrites them. |
| **Yes** | **Connection sync only.** The dashboard, its charts and its floor maps are left untouched. The connection is updated from `config.json` — name, URI, timezone — or recreated if it has been deleted. |

The connection sync runs on every start, which is how a password change in
`config.json` takes effect.

> **When the check fails, the import assumes "No".** The script treats a dashboard
> as new not only when it is absent, but also when the ZIP has no dashboard UUID
> (`⚠️ No dashboard UUID found in zip — treating as new`) or the lookup itself
> errors (`⚠️ Error checking dashboard existence`). In the second case the dashboard
> may exist: the full import then replaces it — Superset imports the dashboard
> itself with overwrite on — losing any layout edits made in the UI. The floor maps
> are regenerated with new UUIDs, so the old floor-map charts and dataset are left
> behind unused. If either message appears, check the dashboard before trusting the
> result.

### What this means in practice

- **Edits made in the UI survive restarts.** Once a dashboard exists, the import
  never overwrites its layout, charts or filters.
- **Changing `floors` on an imported dashboard does nothing.** Floor maps are
  generated only on a full import.
- **A newer version of a template ZIP does not reach an existing deployment.** A
  re-export of the same dashboard keeps its UUID, so the import sees it as already
  present and skips it — even if the ZIP's file name and `path` change.
- **To force a full import**, the dashboard has to be absent. Two ways:
  - **Delete it in the UI** and rerun the import. This is enough to regenerate floor
    maps: they are created with new UUIDs, so the new ones are imported (the old floor
    charts and their dataset stay behind and can be deleted). It is **not** enough to
    apply a new template version: the template's own charts and datasets still
    exist, and the import reuses them unchanged. Delete those too, or use the next
    option.
  - **Start from an empty metadata database.** `down -v` empties it, and deletes every
    dashboard, chart, user and saved connection on the stack — take a backup first
    ([07](07-operations.md)).

### One template, one property per instance

The import cannot place the same dashboard template twice on one instance, for
example two properties both using `property_dashboard_template_v6.zip`:

- **Same `path` in two entries.** Both entries get the same connection UUID. The first
  is imported; the second finds the dashboard present and syncs the connection, so
  the shared connection ends up with the **second** entry's credentials. The first
  property's dashboard then queries the second property's database.
- **A copy of the ZIP under another name.** The connections are separate, but the
  dashboard UUID inside the ZIP is the same, so the second entry is skipped as
  already imported. Its connection is created with no dashboard using it.

Exporting the dashboard again under a new dashboard UUID does not help either: the
export still carries the template's dataset UUIDs, and the import reuses existing
datasets unchanged, so the second dashboard's template charts would query the first
property's database. Only the generated floor maps, which get fresh UUIDs, would be
correct.

**Run one instance per property.**

Customer Journey charts are not generated; they are imported as they are in the ZIP.

## Adding a dashboard

1. Build or edit the dashboard in Superset, then export it: **⋮ → Download → Export YAML**
   on the dashboard, or **Export** from the Dashboards list. Both produce a ZIP.
2. Copy the ZIP into `lauretta/dashboards/`.
3. Add an entry to `config.json`, with `floors` if it needs floor maps.
4. Put any floor images in `lauretta/images/`.
5. Rerun the import (above).

Committed dashboard ZIPs contain no passwords — Superset masks them on export, and
the import injects them from `config.json`.

## Floor maps

### Floor images

Images are read from `lauretta/images/` at runtime (it is bind-mounted into every
container), so adding or replacing one needs no rebuild.

Supported: `.jpeg`, `.jpg`, `.png`, `.gif`, `.webp`.

In mode 3, the directory must be writable by uid 1000 — see
[06 Secure mode](06-secure-mode.md).

### The generated dataset and charts

When `floors` is set, the import creates one shared dataset, `Floor Map Summary`,
with these columns:

`floor_id`, `zone_name`, `name`, `layer`, `category`, `points`, `total_footfall`,
`percentage_of_prop`, `event_time`

and one chart per floor, filtered on that floor's `id` through a chart-level
`floor_id` adhoc filter, with
`cols = ['name', 'category', 'points', 'total_footfall', 'percentage_of_prop', 'layer']`.
The charts go into a Map View tab, and are added to the scope of the dashboard's
native filters.

Filtering behaviour:

- `unit_name` and `unit_group_name` are not columns of `Floor Map Summary`. The plugin
  reads those dashboard filter values from `extraFormData.filters` in `transformProps`.
- **Polygon mode** applies them client-side, to the Retail layer only.
- **Heatmap mode** ignores them and uses every row, so density stays accurate.

### Image lifecycle

Users can upload a floor image from the Floor Map chart's controls instead of
placing it in `lauretta/images/`:

- `POST /api/v1/lauretta/images/upload` stores the file in `lauretta/images/tmp/`
  under a sanitised, randomised name, and returns
  `/api/v1/lauretta/images/tmp/<file>`. The file's magic bytes must match its
  extension.
- When a Floor Map chart is saved (created or updated) with a `tmp/` image, the file
  moves to `lauretta/images/customs/` and the chart is rewritten to point there.
- When a chart's image changes, the old `customs/` file is deleted. When a chart is
  deleted, its `customs/` file is deleted.
- Celery beat runs `lauretta.cleanup_temp_images` daily at 00:00 and empties `tmp/`.
  Uploads that were never saved into a chart disappear then.

`lauretta/images/customs/` and `tmp/` are runtime state, not source — do not commit
them.

### The `/api/v1/lauretta` routes

| Route | Returns |
|---|---|
| `GET /api/v1/lauretta/floors` | `[{name, image}]` for every floor in `config.json`, deduplicated by name |
| `GET /api/v1/lauretta/images/<ref>` | The image file. `<ref>` is a filename in `lauretta/images/`, or `customs/<file>`, or `tmp/<file>` |
| `POST /api/v1/lauretta/images/upload` | `{file_name, public_url}`; form field `file` |

All three require a logged-in Superset session in every mode and answer
`401 {"error": "Authentication required"}` otherwise. They are registered directly on
the Flask app, outside Flask-AppBuilder's permission layer, so any logged-in user can
call them regardless of role.

## Inspecting a dashboard ZIP

```bash
unzip -l lauretta/dashboards/property_dashboard_template_v6.zip
mkdir /tmp/dashboard_inspect
unzip lauretta/dashboards/property_dashboard_template_v6.zip -d /tmp/dashboard_inspect
cat /tmp/dashboard_inspect/*/databases/*.yaml
```

Import log messages and their fixes are in
[09 Verification and troubleshooting](09-verification-and-troubleshooting.md).
