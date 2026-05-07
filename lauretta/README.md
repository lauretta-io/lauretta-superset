# Lauretta - Custom Superset Dashboard Management

Complete guide for managing custom dashboards in Apache Superset with automatic import and database password injection.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Quick Start (5 Minutes)](#quick-start-5-minutes)
  - [Dev vs. Prod](#dev-vs-prod)
- [Project Structure](#project-structure)
- [Dashboard Configuration](#dashboard-configuration)
  - [How It Works](#how-it-works)
  - [Usage](#usage)
- [Alerts & Reports](#alerts--reports)
- [White Labeling](#white-labeling)
- [Troubleshooting](#troubleshooting)
- [Advanced Topics](#advanced-topics)

---

## Overview

### Features

✅ **Automatic Dashboard Import** - Dashboards are imported automatically on container startup  
✅ **Database Information Injection** - Database information is injected directly into dashboard files  
✅ **Selective Import** - Only dashboards listed in `config.json` are imported  
✅ **No Example Data** - Example datasets are disabled by default  
✅ **Overwrite Protection** - Existing dashboards are updated safely  
✅ **Automatic Tmp Cleanup** - Uploaded temp map images are cleaned every minute  
✅ **Git-Safe** - Sensitive config files are automatically ignored  

### What's Different?

- **Standard Superset**: Requires manual dashboard import and password configuration via UI
- **Lauretta Superset**: Fully automated - just add your dashboard ZIP and password to config

---

## Quick Start (5 Minutes)

### First Time Setup

```bash
# 1. Clone repository
git clone <your-repo-url>
cd lauretta-superset

# 2. Configure dashboard passwords
cd lauretta/dashboards
cp config.example.json config.json
nano config.json  # Update your database information

# 3. Start Superset
cd ..
docker-compose -f docker-compose-non-dev.yml up -d --build

# 5. Access Superset
# Open: http://localhost:8088
# Login: admin / admin
```

### Quick Test

After setup, your dashboards should be visible immediately in the Superset UI under the "Dashboards" menu.

### Dev vs. prod
There are actually two ways to spin up a superset instance
1. **Development**: `docker compose up` --> uses `docker-compose.yml`
    - this spins up with `superset_node` container which allows hot-module reloading (HMR) and will rebuild the app as changes are made
    - accessed via http://localhost:9000
    - only available via localhost, blocks access from other hosts
    - use this for development and testing changes

2. **Production**: `docker compose -f docker-compose-non-dev.yml up -d --build`
    - builds the app before serving, after build changes will require rebuild to take effect
    - accessed via http://localhost:8088
    - allows access from other hosts
    - use this for client-facing deployed instances

---

## Project Structure

```
lauretta-superset/
├── lauretta/
│   ├── README.md                      ← This file
│   ├── images/                        ← Floor plan images (e.g., floor_CF.jpeg)
│   └── dashboards/
│       ├── config.json                ← Your config (git-ignored)
│       ├── config.example.json        ← Config template
│       ├── .gitignore                 ← Protects sensitive files
│       └── *.zip                      ← Your dashboard files
├── docker/
│   ├── docker-init.sh                 ← Container initialization
│   └── import-dashboards.py           ← Dashboard import logic
└── docker-compose-non-dev.yml         ← Docker orchestration
```

---

## Dashboard Configuration

### 1. Property dashboard template

**File**: `lauretta/dashboards/config.json`

```json
{
  "dashboards": [
    {
      "path": "/lauretta/dashboards/property_dashboard_template_v{}.zip",
      "connections": {
        "database_display_name": "my_database",
        "host": "localhost",
        "port": 5432,
        "username": "your_username",
        "password": "your_password",
        "db": "your_database_name",
        "timezone": "UTC"
      },
      "floors": [
        {
          "id": 1,
          "name": "FLOOR NAME",
          "image": "floor_plan_F1.jpeg"
        },
        {
          "id": 2,
          "name": "FLOOR NAME 2",
          "image": "floor_plan_F2.png"
        }
      ]
    }
  ]
}
```

**Structure**:

- `dashboards` - Array of dashboard configurations
  - `path` (required) - Path to the dashboard ZIP file (usually starts with `/lauretta/`)
  - `connections` (required) - Database connection object for this dashboard
    - `database_display_name` (required) - **Display name for this database connection** (shown in Superset UI)
    - `host` (required) - Database host
    - `port` (required) - Database port
    - `username` (required) - Database username
    - `password` (required) - Database password to inject
    - `db` (required) - Database name
    - `timezone` (optional) - IANA timezone identifier for this dashboard connection only (e.g., `"Asia/Singapore"`, `"UTC"`, `"America/New_York"`). Sets PostgreSQL session timezone via `engine_params.connect_args.options = -c timezone=<tz>`.
  - `floors` (optional) - Array of floor plan entries for Floor Map charts
    - `id` (required) - **The floor's ID as stored in the database** — this is NOT the display order; it must match the actual floor ID value in your data source
    - `name` (required) - Display name of the floor (e.g., `"Ground"`, `"Level 1"`)
    - `image` (required) - Floor image reference. Usually a filename in `lauretta/images/` (e.g., `"floor_plan_F1.jpeg"`), but `/api/v1/lauretta/images/...`, absolute `/...`, or `http(s)://...` values are also supported.

> ⚠️ **Important — `id` is a database ID, not a sequence number.**  
> The `id` value must match the floor identifier in your database (e.g., the value stored in the `floor_id` column of your dataset). Setting it to `1, 2, 3...` by order will cause the wrong floor map to display if your database uses different IDs.

### 2. Floor Plan Images

Floor plan images are served directly from the `lauretta/images/` folder — **no rebuild required** when adding or changing images.

**Steps to add / update a floor image**:

1. Copy your image file into `lauretta/images/`:
   ```bash
   cp ~/Downloads/floor_plan_L1.jpeg lauretta/images/
   ```

2. Update `lauretta/dashboards/config.json` so the floor entry references the correct filename:
   ```json
   {
     "id": 1,
     "name": "Level 1",
     "image": "floor_plan_L1.jpeg"
   }
   ```

3. Restart the init container to apply import/config updates:
   ```bash
   docker compose restart superset-init
   ```

Images are served at runtime from:
```
GET /api/v1/lauretta/images/<filename>
```

**Supported image formats**: `.jpeg`, `.jpg`, `.png`, `.gif`, `.webp`

### 3. Temporary Upload Folder Lifecycle (`lauretta/images/tmp`)

`superset_config.py` provides an upload endpoint and cleanup flow for temporary floor-map images:

- `POST /api/v1/lauretta/images/upload` saves image files to `/app/lauretta/images/tmp` and returns a public URL like `/api/v1/lauretta/images/tmp/<file>`.
- When a Floor Map chart is created or updated, `floor_image` values from `/tmp/` are moved into `/app/lauretta/images/customs/` automatically.
- When a chart is updated and image changes, old custom images are deleted automatically.
- When a chart is deleted, the related custom image is deleted automatically.
- Celery beat task `lauretta.cleanup_temp_images` runs in 12AM and deletes all files in `/app/lauretta/images/tmp`.

**What happens during import**:

1. The script extracts your dashboard ZIP
2. Computes a stable **database UUID** from the dashboard `path`
3. Checks whether the dashboard UUID from ZIP already exists in Superset
4. If dashboard already exists: updates only the database connection (name + URI)
5. If dashboard is new: patches ZIP database YAML (`database_name` inside export YAML, `sqlalchemy_uri`, `uuid`), updates dataset `database_uuid`, optionally generates floor map datasets/charts, then imports using a temporary `.imported.zip`

If `connections.timezone` is set, import also writes PostgreSQL session timezone into DB `extra.engine_params.connect_args.options = -c timezone=<tz>`.

**IMPORTANT - About `database_display_name`**:

`database_display_name` is the connection label in Superset.

- If dashboard `path` stays the same, re-running import updates the existing connection record (same generated UUID).
- Changing only `database_display_name` usually renames/updates that connection.
- Changing dashboard `path` changes the generated database UUID and can create a new database record.

**To simply update database credentials** (host, password, etc.):
- ✅ Update only `host`, `port`, `username`, `password`, or `db` fields
- ✅ Re-run the import

**To use a different database**:
- Keep same `path` to update existing connection details
- Or use a different dashboard `path` to create a separate database UUID/record
- List both in the `dashboards` array

**Important Security Notes**:
- ⚠️ `config.json` is **git-ignored** — never commit database credentials to git
- ✅ `config.example.json` is the template — safe to commit
- 📝 Only dashboards listed here will be imported

### 4. Environment Variables

**File**: `docker/.env`

```bash
# Removes the "development" tag in the Nav Bar
SUPERSET_ENV=production

# Disable example data loading
SUPERSET_LOAD_EXAMPLES=no

# Database configuration
DATABASE_PASSWORD=superset
DATABASE_USER=superset
DATABASE_DB=superset

# Email configuration for Alerts & Reports
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_SSL_SERVER_AUTH=true
SMTP_SSL=false
SMTP_USER=
SMTP_PASSWORD=
SMTP_MAIL_FROM=
EMAIL_REPORTS_SUBJECT_PREFIX=[Superset]
```

**Key Settings**:
- `SUPERSET_ENV=production` - Removes the "development" tag in the Nav Bar
- `SUPERSET_LOAD_EXAMPLES=no` - Prevents example dashboards from loading
- Set database credentials for the Superset metadata database
- Set `SMTP_*` variables for Alerts & Reports email delivery

---

### How It Works


#### Startup Sequence

```
1. docker-compose -f docker-compose-non-dev.yml up -d --build
  ↓
2. superset-init container starts
  ↓
3. docker-init.sh runs:
  - Creates database tables
  - Creates admin user
  - Sets up roles/permissions
  ↓
4. import-dashboards.py executes:
  - Reads config.json
  - For each dashboard:
    a. Builds a stable database UUID from dashboard path
    b. Checks if dashboard UUID from ZIP already exists in metadata DB
    c. If exists: updates only database connection (name + URI)
    d. If new: extracts ZIP and patches DB YAML + dataset UUIDs
    e. If floors are configured: generates floor map datasets/charts and patches dashboard layout
    f. Re-zips to `<name>.imported.zip`, imports with `superset import-dashboards`, and cleans temp files
  ↓
5. Superset app starts
  ↓
6. Dashboards ready to use!
```

#### Dashboard & Floor Map Import Process

The `import-dashboards.py` script automates database credential injection and floor chart creation:

1. **Extracts** the dashboard ZIP file using the `path` from config.json.
2. **Generates target database UUID** from dashboard path and syncs DB connection by UUID.
3. **Checks dashboard existence** by reading dashboard UUID from ZIP.
4. **If dashboard exists**, only updates DB connection display label (`database_display_name`) and `sqlalchemy_uri`.
5. **If dashboard is new**, patches `databases/*.yaml` (`database_name` field in export YAML, `sqlalchemy_uri`, `uuid`) and all `datasets/*.yaml` `database_uuid`.
6. **When `floors` is set**, generates MAP datasets/charts from built-in templates and updates dashboard layout (Map View tab, rows, filter scope).
7. **Creates temporary `.imported.zip`**, imports it, then removes extracted folders and temp ZIP.

This ensures:
- ✅ Database credentials are injected directly into dashboard files
- ✅ Floor plan images served at runtime from `lauretta/images/`
- ✅ No manual UI configuration required
- ✅ Re-runs update database connection details safely


When floor maps are generated by `docker/import-dashboards.py`, the default dataset is `Floor Map Summary` and includes these primary columns:

- `floor_id`
- `zone_name`
- `name`
- `layer`
- `category`
- `points`
- `total_footfall`
- `percentage_of_prop`
- `event_time`

Notes for developers:

- `unit_name` and `unit_group_name` are not physical columns in `Floor Map Summary`.
- The floor-map plugin extracts dashboard filter values for `unit_name` and `unit_group_name` from `extraFormData.filters` in `transformProps`.
- In polygon mode, filtering is applied client-side to Retail layer only.
- In heatmap mode, dashboard unit filters are intentionally skipped; all rows are used (no client-side unit filtering) so density stays accurate.
- Generated chart defaults use `cols = ['name', 'category', 'points', 'total_footfall', 'percentage_of_prop', 'layer']` and a chart-level `floor_id` adhoc filter.

---

### Usage

#### Adding a New Dashboard

1. **Export from Superset UI**:
   - Navigate to your dashboard
   - Click "⋮" menu → "Export"
   - Download the ZIP file

2. **Add to project**:
   ```bash
   # Copy dashboard to dashboards directory
   cp ~/Downloads/my_dashboard.zip lauretta/dashboards/

   # Update config.json
   nano lauretta/dashboards/config.json
   ```

3. **Configure in config.json**:

   ```json
   {
     "dashboards": [
       {
         "path": "/lauretta/dashboards/my_dashboard.zip",
         "connections": {
           "database_display_name": "my_database",
           "host": "localhost",
           "port": 5432,
           "username": "username",
           "password": "db_password",
           "db": "database_name",
           "timezone": "UTC"
         },
         "floors": [
           { "id": 1, "name": "Ground", "image": "floor_ground.jpeg" },
           { "id": 2, "name": "Level 1", "image": "floor_l1.jpeg" }
         ]
       }
     ]
   }
   ```

  > **Note**: Place all floor image files in `lauretta/images/` before restarting.
  > Floor-map generation applies on first import of a dashboard UUID.

4. **Restart to import**:
   ```bash
   docker compose restart superset-init
   docker compose logs -f superset-init
   ```

---

#### Updating Database Credentials

**If you just need to update database credentials** (host, port, password, etc.):

- Edit config.json
- Update only: `host`, `port`, `username`, `password`, or `db`
- Re-run the import script
- Keep the dashboard `path` unchanged to update the same database UUID record

Example:
```json
{
  "dashboards": [
    {
      "path": "/lauretta/dashboards/my_dashboard.zip",
      "connections": {
        "database_display_name": "my_database", // ← Can change label if needed
        "host": "new-host.com",                 // ← Update this
        "port": 5432,                           // ← Or this
        "username": "new_username",             // ← Or this
        "password": "new_password",             // ← Or this
        "db": "new_database_name"               // ← Or this
      }
    }
  ]
}
```

---
#### Adding or Updating a Floor Image

1. **Copy the image** into `lauretta/images/`:
   ```bash
   cp ~/Downloads/new_floorplan.jpeg lauretta/images/
   ```

2. **Update `config.json`** to reference the filename:
   ```json
   { "id": 3, "name": "Level 2", "image": "new_floorplan.jpeg" }
   ```

3. **Restart** the init container:
   ```bash
   docker compose restart superset-init
   ```

No rebuild of the frontend is needed — images are fetched from `lauretta/images/` at runtime.

> ⚠️ If the dashboard was already imported, the script skips dashboard re-import and only updates DB connection settings.
> To apply changed `floors`/chart layout, re-import as a fresh dashboard metadata state (for example after `docker compose down -v`).

---
#### Manual Import (Testing)

```bash
docker exec -it superset_app superset import-dashboards \
  -p /app/lauretta/dashboards/property_demo.zip \
  -u admin
```

#### Viewing Logs

```bash
# Init process logs (import happens here)
docker-compose -f docker-compose-non-dev.yml logs -f superset-init

# Superset application logs
docker-compose -f docker-compose-non-dev.yml logs -f superset

# All services
docker-compose -f docker-compose-non-dev.yml logs -f
```

---

## Alerts & Reports

### For Developers (Code Setup)

Use this section if you maintain infrastructure/config for scheduled alerts and reports.

1. **Ensure these lines exist in** `docker/pythonpath_dev/superset_config.py`:

    ```python
    FEATURE_FLAGS = {
      "ALERT_REPORTS": True,
      "ALERT_REPORT_TABS": True,
      "ALLOW_ADHOC_SUBQUERY": True,
      "ENABLE_TEMPLATE_PROCESSING": True,
    }
    ALERT_REPORTS_NOTIFICATION_DRY_RUN = False

    class CeleryConfig:
      broker_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_CELERY_DB}"
      imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
      )
      result_backend = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_RESULTS_DB}"
      beat_schedule = {
        "reports.scheduler": {
          "task": "reports.scheduler",
          "schedule": crontab(minute="*", hour="*"),
        },
        "reports.prune_log": {
          "task": "reports.prune_log",
          "schedule": crontab(minute=10, hour=0),
        },
        "lauretta.cleanup_temp_images": {
          "task": "lauretta.cleanup_temp_images",
          "schedule": crontab(hour="0", minute="0")
        }
      }

    CELERY_CONFIG = CeleryConfig

    def _env_bool(name: str, default: bool) -> bool:
      value = os.getenv(name)
      if value is None:
        return default
      return value.strip().lower() in {"1", "true", "yes", "on"}

    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_STARTTLS = _env_bool("SMTP_STARTTLS", True)
    SMTP_SSL_SERVER_AUTH = _env_bool("SMTP_SSL_SERVER_AUTH", True)
    SMTP_SSL = _env_bool("SMTP_SSL", False)
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_MAIL_FROM = os.getenv("SMTP_MAIL_FROM", SMTP_USER)
    EMAIL_REPORTS_SUBJECT_PREFIX = os.getenv("EMAIL_REPORTS_SUBJECT_PREFIX", "[Superset] ")

    WEBDRIVER_TYPE = "chrome"
    WEBDRIVER_OPTION_ARGS = [
      "--headless",
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-extensions",
    ]
    # This is for internal use, you can keep http
    WEBDRIVER_BASEURL = "http://superset:8088"  # When running using docker compose use "http://superset_app:8088"
    # This is the link sent to the recipient. Change to your domain, e.g. https://superset.mydomain.com
    WEBDRIVER_BASEURL_USER_FRIENDLY = "http://localhost:8088"
    ```

2. **Set email variables in** `docker/.env` (example):
  ```bash
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_STARTTLS=true
  SMTP_SSL_SERVER_AUTH=true
  SMTP_SSL=false
  SMTP_USER=your_user@gmail.com
  SMTP_PASSWORD=your_app_password
  SMTP_MAIL_FROM=your_user@gmail.com
  EMAIL_REPORTS_SUBJECT_PREFIX=[Superset]
  ```

3. **Ensure worker services are running**:
  ```bash
  docker compose -f docker-compose-non-dev.yml up -d superset-worker superset-worker-beat
  ```

4. **Apply config changes**:
  ```bash
  docker compose -f docker-compose-non-dev.yml restart superset superset-worker superset-worker-beat
  ```

5. **Verify scheduler activity**:
  ```bash
  docker logs --tail 200 superset_worker
  docker logs --tail 200 superset_worker_beat
  ```

> ⚠️ If logs show `ALERT_REPORTS_NOTIFICATION_DRY_RUN is enabled`, emails are intentionally not sent.

### For External Users (UI Only)

Use this section if you only create alerts/reports from the Superset UI.

1. Go to **Settings** → **Alerts & Reports**.
2. Choose tabs **Report** or **Alert**.
3. Click **+ Report** or **+ Alert** to add new.
4. Configure following the UI:
  - Name and schedule (cron/time)
  - Recipients (email)
  - For alerts: condition and threshold
5. Save and test.

**About dashboard tabs:**
- By default, a scheduled dashboard report usually captures the currently configured/default tab state.
- To capture specific tabs or multiple tabs, this must be enabled by developers (`ALERT_REPORT_TABS`) and configured at schedule level.
- External users can request this from maintainers if tab screenshots are required.

---

## White Labeling

The white labeling options provided for are:
* APP_ICON: banner image displayed in the Nav Bar
* APP_NAME: name displayed in browser tab

By default, the app will use the standard Superset banner and "Superset" app name. 
To use a custom banner image and app name, follow these steps:

1. add custom banner image to `/lauretta/branding` directory
2. uncomment and modify APP_ICON and APP_NAME variables in `docker/pythonpath_dev/superset_config.py` as necessary

---

## Troubleshooting


### Dashboard Not Importing?

**Check logs:**
```bash
docker-compose -f docker-compose-non-dev.yml logs superset-init | grep -i dashboard
```

**Common script messages and fixes:**

1. **Missing or invalid `connections` object**
  ```
  ⚠️ Missing or invalid connections config for dashboard: <path>
  ```
  **Fix:** Ensure each dashboard has a valid `connections` object with `host`, `port`, `username`, `password`, and `db`.

2. **Could not read dashboard UUID from ZIP**
  ```
  ⚠️ Could not read dashboard UUID from zip: <error>
  ```
  **Fix:** Validate ZIP format and dashboard YAML contents.

3. **No dashboard UUID found in ZIP (treated as new)**
  ```
  ⚠️ No dashboard UUID found in zip — treating as new
  ```
  **Fix:** Re-export dashboard with complete metadata if this is unexpected; otherwise import continues as first-time setup.

4. **Error while checking dashboard existence**
  ```
  ⚠️ Error checking dashboard existence: <stderr>
  ```
  **Fix:** Verify Superset app context and metadata DB connectivity.

5. **Dashboard already exists (no re-import)**
  ```
  ✅ Dashboard already exists in database — only updating database connection.
  ```
  **Fix:** This is expected. If you intended to regenerate floor charts/layout, re-import as a fresh metadata state.

6. **Missing folders in extracted ZIP**
  ```
  ⚠️ No databases/ folder in extracted ZIP
  ```
  or
  ```
  ⚠️ No datasets/ folder in extracted ZIP
  ```
  **Fix:** Re-export dashboard ZIP from Superset and retry.

7. **Map layout/template issues in dashboard YAML**
  ```
  ⚠️ No dashboard YAML found
  ```
  or
  ```
  ⚠️ Could not resolve ROOT/GRID in dashboard layout
  ```
  **Fix:** Ensure the exported ZIP contains a valid `dashboards/*.yaml` with standard layout nodes.

8. **Could not extract ZIP for patching**
  ```
  ❌ Could not find or create extraction directory
  ```
  or
  ```
  ❌ Could not extract ZIP for credential patching
  ```
  **Fix:** Verify file path and ZIP validity.

9. **Cannot find DB UUID in ZIP (fallback used)**
  ```
  ⚠️ Cannot find UUID in ZIP: <zip_path>; using generated target UUID
  ```
  **Fix:** Usually safe; if you need strict UUID continuity, re-export dashboard ZIP with database metadata.

10. **Database update via app context failed**
  ```
  ❌ Error updating database via Python app-context: <stderr>
  ```
  **Fix:** Check Superset init state, metadata DB health, and model migration status.

11. **No floors configured (informational)**
  ```
  ℹ️ No floors configured, patching database credentials only...
  ```
  **Fix:** Add `floors` entries in `config.json` if map datasets/charts should be generated.

### Example Data Still Loading?

**Verify environment variable:**
```bash
docker-compose -f docker-compose-non-dev.yml exec superset env | grep SUPERSET_LOAD_EXAMPLES
# Should output: SUPERSET_LOAD_EXAMPLES=no
```

**If not set:**
1. Edit `docker/.env`
2. Set `SUPERSET_LOAD_EXAMPLES=no`
3. Restart:
  ```bash
  docker-compose -f docker-compose-non-dev.yml down
  docker-compose -f docker-compose-non-dev.yml up -d --build
  ```

### Database Connection Failed?

**Check database credentials:**
```bash
# Test connection from container
docker-compose -f docker-compose-non-dev.yml exec superset superset db upgrade
```

**Verify DB sync logs:**
```bash
docker compose -f docker-compose-non-dev.yml logs superset-init | \
  grep -E "TARGET DB UUID|Database sync complete|Dashboard already exists"
```

> Note: the script imports from a temporary `.imported.zip` and deletes it after import; it does not overwrite your original dashboard ZIP.

### Container Won't Start?

```bash
# View all logs
docker-compose -f docker-compose-non-dev.yml logs

# Rebuild from scratch
docker-compose -f docker-compose-non-dev.yml down -v  # ⚠️ This deletes data!
docker-compose -f docker-compose-non-dev.yml up -d --build
```

### Permission Issues?

```bash
# Fix file permissions
chmod 600 lauretta/dashboards/config.json
```

---

## Advanced Topics

### Custom Database Configuration

If your dashboard uses a custom database connection:

1. **Add database to Superset** (if not in dashboard export):
   ```bash
   docker-compose -f docker-compose-non-dev.yml exec superset superset set-database-uri \
     --database-name "My Database" \
     --uri "postgresql://user:password@host:port/dbname"
   ```

2. **Or use Superset UI**:
   - Settings → Database Connections → "+ Database"
   - Enter connection details
   - Test connection
   - Save

### Debugging the Import Script

Run the import script manually with verbose output:

```bash
docker exec -it superset_init python /app/docker/import-dashboards.py
```

Or tail the logs while it runs:

```bash
docker compose -f docker-compose-non-dev.yml logs -f superset-init
```

### Inspecting Dashboard ZIP

To see what's inside a dashboard ZIP:

```bash
# List contents
unzip -l lauretta/dashboards/property_demo.zip

# Extract to view
mkdir /tmp/dashboard_inspect
unzip lauretta/dashboards/property_demo.zip -d /tmp/dashboard_inspect
cat /tmp/dashboard_inspect/databases/*.yaml
```

### Custom Import Logic

To modify the import behavior, edit:
- **Import script**: `docker/import-dashboards.py`
- **Init script**: `docker/docker-init.sh`

After changes:
```bash
docker-compose restart superset-init
```

---

## For New Developers

### First Time Setup Checklist

- [ ] Clone the repository
- [ ] Copy `config.example.json` to `config.json`
- [ ] Add dashboard passwords and connection info to `config.json`
- [ ] Verify `SUPERSET_LOAD_EXAMPLES=no` in `docker/.env`
- [ ] Run `docker-compose -f docker-compose-non-dev.yml up --build`
- [ ] Check logs: `docker-compose -f docker-compose-non-dev.yml logs -f superset-init`
- [ ] Access Superset at http://localhost:8088
- [ ] Login with admin/admin
- [ ] Verify dashboards are visible

### Understanding the Flow

```
Developer → Adds dashboard ZIP + config
           ↓
Docker Compose → Starts containers
           ↓
Init Script → Imports dashboards with passwords
           ↓
Superset → Ready to use with working database connections
```

### Key Files to Know

| File | Purpose | Should Commit? |
|------|---------|----------------|
| `config.json` | Dashboard passwords & floor config | ❌ No (git-ignored) |
| `config.example.json` | Config template | ✅ Yes |
| `docker-init.sh` | Container startup | ✅ Yes |
| `import-dashboards.py` | Import logic | ✅ Yes |
| `*.zip` | Dashboard files | ✅ Yes (no passwords) |
| `lauretta/images/*.jpeg` | Floor plan images | ✅ Yes (no secrets) |

---

## Support

### Getting Help

1. Check the [Troubleshooting](#troubleshooting) section
2. Review logs: `docker-compose -f docker-compose-non-dev.yml logs`
3. Check Superset docs: https://superset.apache.org/docs/

---

## License

This project extends Apache Superset, which is licensed under the Apache License 2.0.

See `LICENSE.txt` for details.

---

**Last Updated**: 7 May 2026  
**Superset Version**: 5.0.0  
**Maintained by**: Lauretta Team
