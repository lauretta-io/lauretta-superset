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
✅ **Password Injection** - Database passwords are injected directly into dashboard files  
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
nano config.json  # Add your database passwords

# 3. Start Superset
# If you haven't built the frontend assets yet,or anything change related to frontend do it now:
cd superset-frontend
npm install
npm run build

cd ..
docker-compose up -d

# 4. Wait for initialization (30-60 seconds)
docker-compose logs -f superset-init

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
│   ├── test-import-dashboard.sh       ← Manual import test script
│   └── dashboards/
│       ├── config.json                ← Your config (git-ignored)
│       ├── config.example.json        ← Config template
│       ├── .gitignore                 ← Protects sensitive files
│       └── *.zip                      ← Your dashboard files
├── docker/
│   ├── docker-init.sh                 ← Container initialization
│   └── import-dashboards.sh           ← Dashboard import logic
└── docker-compose.yml                 ← Docker orchestration
```

---

## Configuration Guide

### 1. Dashboard Configuration

**File**: `lauretta/dashboards/config.json`

```json
{
  "dashboards": [
    {
      "name": "property_dashboard.zip",
      "connections": [
        {
          "database_name": "Property Database",
          "password": "your_secure_password_here"
        }
      ]
    },
    {
      "name": "sales_dashboard.zip",
      "connections": [
        {
          "database_name": "Sales DB",
          "password": "sales_db_password"
        },
        {
          "database_name": "Analytics DB",
          "password": "analytics_db_password"
        }
      ]
    }
  ]
}
```

**Structure**:
- `dashboards` - Array of dashboard configurations
  - `name` (required) - ZIP filename in `lauretta/dashboards/` directory
  - `connections` (required) - Array of database connections for this dashboard
    - `database_name` (required) - Name of the database connection in the dashboard
    - `password` (required) - Database password to inject

**Features**:
- ✅ **Multiple Databases per Dashboard** - A single dashboard can connect to multiple databases
- ✅ **Individual Passwords** - Each database connection gets its own password

**Important Notes**:
- ⚠️ `config.json` is **git-ignored** - never commit passwords to git
- ✅ `config.example.json` is the template - safe to commit
- 📝 Only dashboards listed here will be imported

### 2. Environment Variables

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
1. docker-compose up
   ↓
2. superset-init container starts
   ↓
3. docker-init.sh runs:
   - Creates database tables
   - Creates admin user
   - Sets up roles/permissions
   ↓
4. import-dashboards.sh executes:
   - Reads config.json
   - For each dashboard:
     a. Unzips dashboard file
     b. Locates database YAML file
     c. Injects password into YAML
     d. Re-zips dashboard
     e. Imports to Superset
   ↓
5. Superset app starts
   ↓
6. Dashboards ready to use!
```

### Password Injection Process

The `import-dashboards.sh` script:

1. **Extracts** dashboard ZIP file
2. **Searches** for database YAML file (matches `database_name`)
3. **Updates** the YAML:
   ```yaml
   sqlalchemy_uri: "postgresql://user:INJECTED_PASSWORD@host/db"
   password: "INJECTED_PASSWORD"
   ```
4. **Re-packages** the modified dashboard
5. **Imports** via Superset CLI

This means **passwords work immediately** without manual UI configuration!

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
         "name": "my_dashboard.zip",
         "connections": [
           {
             "database_name": "My Database",
             "password": "db_password"
           }
         ]
       }
     ]
   }
   ```

   **For dashboards with multiple databases**:
   ```json
   {
     "dashboards": [
       {
         "name": "my_dashboard.zip",
         "connections": [
           {
             "database_name": "Primary DB",
             "password": "primary_password"
           },
           {
             "database_name": "Secondary DB",
             "password": "secondary_password"
           }
         ]
       }
     ]
   }
   ```

4. **Restart to import**:
   ```bash
   docker-compose restart superset-init
   docker-compose logs -f superset-init
   ```

### Updating a Dashboard

1. **Replace the ZIP file**:
   ```bash
   cp ~/Downloads/updated_dashboard.zip lauretta/dashboards/property_dashboard.zip
   ```

2. **Restart init container**:
   ```bash
   docker-compose restart superset-init
   ```

The dashboard will be re-imported with the latest changes.

### Manual Import (Testing)

Test dashboard import without restarting containers:

```bash
cd lauretta
./test-import-dashboard.sh property_dashboard.zip
```

Or directly:

```bash
docker exec -it superset_app superset import-dashboards \
  -p /app/lauretta/dashboards/property_dashboard.zip \
  -u admin
```

### Viewing Logs

```bash
# Init process logs (import happens here)
docker-compose logs -f superset-init

# Superset application logs
docker-compose logs -f superset

# All services
docker-compose logs -f
```

---

## Troubleshooting

### Dashboard Not Importing?

**Check logs**:
```bash
docker-compose logs superset-init | grep -i dashboard
```

**Common issues**:

1. **Dashboard not in config.json**
   ```
   ❌ Dashboard "my_dashboard.zip" not found in config
   ```
   **Fix**: Add dashboard to `config.json`

2. **Config file missing**
   ```
   ❌ Config file not found: /app/lauretta/dashboards/config.json
   ```
   **Fix**: `cp config.example.json config.json`

3. **Password not provided**
   ```
   ❌ Password not provided for dashboard "property_dashboard.zip"
   ```
   **Fix**: Add `"password"` field in `config.json`

4. **Database YAML not found**
   ```
   ⚠️ Database YAML not found for "My Database"
   ```
   **Fix**: Check `database_name` matches the name in your dashboard export

### Example Data Still Loading?

**Verify environment variable**:
```bash
docker-compose exec superset env | grep SUPERSET_LOAD_EXAMPLES
# Should output: SUPERSET_LOAD_EXAMPLES=no
```

**If not set**:
1. Edit `docker/.env`
2. Set `SUPERSET_LOAD_EXAMPLES=no`
3. Restart: `docker-compose down && docker-compose up -d`

### Database Connection Failed?

**Check database credentials**:
```bash
# Test connection from container
docker-compose exec superset superset db upgrade
```

**Verify password injection**:
```bash
# Check if password was injected
docker-compose exec superset python3 << 'EOF'
import zipfile
import yaml

with zipfile.ZipFile('/app/lauretta/dashboards/property_dashboard.zip') as z:
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
docker-compose logs

# Rebuild from scratch
docker-compose down -v  # ⚠️ This deletes data!
docker-compose build --no-cache
docker-compose up -d
```

### Permission Issues?

```bash
# Fix file permissions
chmod +x docker/import-dashboards.sh
chmod +x lauretta/test-import-dashboard.sh

# Fix config file
chmod 600 lauretta/dashboards/config.json
```

---

## Advanced Topics

### Custom Database Configuration

If your dashboard uses a custom database connection:

1. **Add database to Superset** (if not in dashboard export):
   ```bash
   docker-compose exec superset superset set-database-uri \
     --database-name "My Database" \
     --uri "postgresql://user:password@host:5432/dbname"
   ```

2. **Or use Superset UI**:
   - Settings → Database Connections → "+ Database"
   - Enter connection details
   - Test connection
   - Save

### Multiple Environments

Use different config files for dev/staging/prod:

```bash
# Development
cp config.dev.json config.json

# Production
cp config.prod.json config.json
```

Add to `.gitignore`:
```
config.json
config.dev.json
config.prod.json
config.staging.json
```

### Debugging Import Script

Run the import script manually with debug output:

```bash
docker-compose exec superset bash -x /app/docker/import-dashboards.sh
```

This shows each command as it executes.

### Inspecting Dashboard ZIP

To see what's inside a dashboard ZIP:

```bash
# List contents
unzip -l lauretta/dashboards/property_dashboard.zip

# Extract to view
mkdir /tmp/dashboard_inspect
unzip lauretta/dashboards/property_dashboard.zip -d /tmp/dashboard_inspect
cat /tmp/dashboard_inspect/databases/*.yaml
```

### Custom Import Logic

To modify the import behavior, edit:
- **Import script**: `docker/import-dashboards.sh`
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
- [ ] Add dashboard passwords to `config.json`
- [ ] Verify `SUPERSET_LOAD_EXAMPLES=no` in `docker/.env`
- [ ] Run `docker-compose up -d`
- [ ] Check logs: `docker-compose logs -f superset-init`
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
| `config.json` | Dashboard passwords | ❌ No (git-ignored) |
| `config.example.json` | Config template | ✅ Yes |
| `docker-init.sh` | Container startup | ✅ Yes |
| `import-dashboards.sh` | Import logic | ✅ Yes |
| `*.zip` | Dashboard files | ✅ Yes (no passwords) |

---

## Support

### Getting Help

1. Check the [Troubleshooting](#troubleshooting) section
2. Review logs: `docker-compose logs`
3. Test manually: `./test-import-dashboard.sh`
4. Check Superset docs: https://superset.apache.org/docs/

---

## License

This project extends Apache Superset, which is licensed under the Apache License 2.0.

See `LICENSE.txt` for details.

---

**Last Updated**: February 2026  
**Superset Version**: 4.1.0  
**Maintained by**: Lauretta Team
