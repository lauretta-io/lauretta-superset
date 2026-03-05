# Lauretta - Custom Superset Dashboard Management

Complete guide for managing custom dashboards in Apache Superset with automatic import and database password injection.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Quick Start (5 Minutes)](#quick-start-5-minutes)
- [Project Structure](#project-structure)
- [Configuration Guide](#configuration-guide)
- [How It Works](#how-it-works)
- [Usage](#usage)
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

## Configuration Guide

### 1. Dashboard Configuration

**File**: `lauretta/dashboards/config.json`

```json
{
  "dashboards": [
    {
      "path": "/lauretta/dashboards/property_demo.zip",
      "connections": {
        "database_display_name": "my_database",
        "host": "localhost",
        "port": 5432,
        "username": "your_username",
        "password": "your_password",
        "db": "your_database_name"
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
  - `floors` (optional) - Array of floor plan entries for Floor Map charts
    - `id` (required) - **The floor's ID as stored in the database** — this is NOT the display order; it must match the actual floor ID value in your data source
    - `name` (required) - Display name of the floor (e.g., `"Ground"`, `"Level 1"`)
    - `image` (required) - Filename of the floor plan image placed in `lauretta/images/` (e.g., `"floor_plan_F1.jpeg"`)

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

3. Restart the init container to re-import chart configuration:
   ```bash
   docker compose restart superset-init
   ```

Images are served at runtime from:
```
GET /api/v1/lauretta/images/<filename>
```

**Supported image formats**: `.jpeg`, `.jpg`, `.png`, `.gif`, `.webp`

**What happens during import**:

1. The script extracts your dashboard ZIP
2. Updates the database YAML file with your connection info:
   - `database_display_name` field
   - `sqlalchemy_uri` (built from host, port, username, password, db)
3. Creates floor map charts — one per floor entry in `floors`
4. Associates each chart with the correct floor image filename
5. Imports the dashboard

**IMPORTANT - About `database_display_name`**:

⚠️ **DO NOT change `database_display_name` after initial import** — this creates a NEW database connection!

- **First import with `database_display_name: "test"`** → Creates database connection named `test`
- **Changing to `database_display_name: "test2"`** → Creates a NEW separate connection `test2`

**To simply update database credentials** (host, password, etc.):
- ✅ Keep `database_display_name` the **same**
- ✅ Update only `host`, `port`, `username`, `password`, or `db` fields
- ✅ Re-run the import

**To use a different database**:
- Create a new dashboard entry with a different `database_display_name`
- List both in the `dashboards` array

**Important Security Notes**:
- ⚠️ `config.json` is **git-ignored** — never commit database credentials to git
- ✅ `config.example.json` is the template — safe to commit
- 📝 Only dashboards listed here will be imported
- 
### 3. Environment Variables

**File**: `docker/.env`

```bash
# Disable example data loading
SUPERSET_LOAD_EXAMPLES=no

# Database configuration
DATABASE_PASSWORD=superset
DATABASE_USER=superset
DATABASE_DB=superset
```

**Key Settings**:
- `SUPERSET_LOAD_EXAMPLES=no` - Prevents example dashboards from loading
- Set database credentials for the Superset metadata database

---

## How It Works


### Startup Sequence

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
    a. Unzips dashboard file (using the path field)
    b. Locates database YAML files in databases/ folder
    c. Updates YAML with:
       - database_display_name from config.json
       - sqlalchemy_uri (connection string)
    d. Creates floor map charts for each entry in the floors array
    e. Associates each floor chart with the image filename from config.json
    f. Creates modified ZIP file
    g. Imports dashboard using modified ZIP
  ↓
5. Superset app starts
  ↓
6. Dashboards ready to use!
```

### Dashboard & Floor Map Import Process

The `import-dashboards.py` script automates database credential injection and floor chart creation:

1. **Extracts** the dashboard ZIP file using the `path` from config.json.
2. **Locates** all database YAML files inside the `databases/` folder.
3. **Updates the YAML file** with:
   - `database_display_name` - Set to the value from config.json
   - `sqlalchemy_uri` - Built from `host`, `port`, `username`, `password`, and `db`
4. **Creates floor map charts** - one chart per entry in the `floors` array, each storing the `image` filename
5. **Images are served at runtime** from `lauretta/images/` via `/api/v1/lauretta/images/<filename>` \u2014 no rebuild needed
6. **Creates modified ZIP** with all updates
7. **Imports the dashboard** into Superset using the modified ZIP file

This ensures:
- \u2705 Database credentials are injected directly into dashboard files
- \u2705 Floor plan images served at runtime from `lauretta/images/`
- \u2705 No manual UI configuration required
- \u2705 New floors/images only need a config update + `docker compose restart superset-init`

---

## Usage

### Adding a New Dashboard

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
           "db": "database_name"
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

4. **Restart to import**:
   ```bash
   docker compose restart superset-init
   docker compose logs -f superset-init
   ```

---

### Updating Database Credentials

**If you just need to update database credentials** (host, port, password, etc.):

✅ **Keep `database_display_name` the SAME**
- Edit config.json
- Update only: `host`, `port`, `username`, `password`, or `db`
- **DO NOT change `database_display_name`**
- Re-run the import script

Example:
```json
{
  "dashboards": [
    {
      "path": "/lauretta/dashboards/my_dashboard.zip",
      "connections": {
        "database_display_name": "my_database", // ← KEEP THIS SAME
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
### Adding or Updating a Floor Image

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

Then run:
```bash
docker compose restart superset-init
```

This updates the existing database connection without creating duplicates.

---
### Manual Import (Testing)

```bash
docker exec -it superset_app superset import-dashboards \
  -p /app/lauretta/dashboards/property_demo.zip \
  -u admin
```

### Viewing Logs

```bash
# Init process logs (import happens here)
docker-compose -f docker-compose-non-dev.yml logs -f superset-init

# Superset application logs
docker-compose -f docker-compose-non-dev.yml logs -f superset

# All services
docker-compose -f docker-compose-non-dev.yml logs -f
```

---

## Troubleshooting


### Dashboard Not Importing?

**Check logs:**
```bash
docker-compose -f docker-compose-non-dev.yml logs superset-init | grep -i dashboard
```

**Common validated errors:**

1. **Dashboard directory not found**
  ```
  Dashboard directory not found: /app/lauretta/dashboards
  ```
  **Fix:** Ensure the directory exists and is mounted correctly.

2. **Config file missing**
  ```
  ⚠️  Config file not found: /app/lauretta/dashboards/config.json
  Please copy config.example.json to config.json
  ```
  **Fix:** Copy config.example.json to config.json and update it.

3. **No dashboards configured**
  ```
  ⚠️  No dashboards configured in config.json
  ```
  **Fix:** Add at least one dashboard entry to config.json.

4. **Skipping dashboard with no path**
  ```
  ⚠️  Skipping dashboard with no path
  ```
  **Fix:** Ensure every dashboard entry has a valid path field.

5. **Dashboard ZIP file missing**
  ```
  ✗ Dashboard not found: /app/lauretta/dashboards/property_demo.zip
  ```
  **Fix:** Ensure the ZIP file exists at the specified path.

6. **Connection count mismatch**
  ```
  ✗ ERROR: Connection count mismatch!
    Config has X connections but ZIP has Y database YAML files
    Both counts must match exactly
  ```
  **Fix:** Adjust config.json or the ZIP so the number of connections matches the number of database YAML files.

7. **Failed to inject connections**
  ```
  ✗ Failed to inject connections
  ```
  **Fix:** Check for missing databases directory, missing YAML files, or invalid YAML in the ZIP.

8. **Import failed**
  ```
  ✗ Import failed
    <error details from Superset CLI>
  ```
  **Fix:** Review the error details, check connection info, and validate all files.

9. **Config file not found (Python error)**
  ```
  ✗ Config file not found: /app/lauretta/dashboards/config.json
  ```
  **Fix:** Ensure config.json exists and is readable.

10. **Invalid JSON in config file**
  ```
  ✗ Invalid JSON in config file: ...
  ```
  **Fix:** Validate config.json syntax (use a JSON linter or editor).

11. **Unexpected error**
  ```
  ✗ Unexpected error: ...
  ```
  **Fix:** Check logs for details, review all fields and files, and ensure all requirements are met.

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

**Verify password injection:**
```bash
# Check if password was injected
# Please remember to use your correct dashboard files
docker-compose -f docker-compose-non-dev.yml exec superset python3 << 'EOF'
import zipfile
import yaml

with zipfile.ZipFile('/app/lauretta/dashboards/property_demo.zip') as z:
  files = [f for f in z.namelist() if 'database' in f.lower() and f.endswith('.yaml')]
  if files:
    with z.open(files[0]) as f:
      data = yaml.safe_load(f)
      print("Password in YAML:", "password" in str(data))
      print("URI:", data.get('sqlalchemy_uri', 'Not found'))
EOF
```

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

**Last Updated**: March 2026  
**Superset Version**: 4.1.0  
**Maintained by**: Lauretta Team
