#!/usr/bin/env bash
#
# Script to automatically import dashboards with password injection
# Reads dashboards from config.json, injects passwords into ZIP files, then imports
#
set -e

echo "######################################################################"
echo "Importing Lauretta Custom Dashboards"
echo "######################################################################"

DASHBOARD_DIR="/app/lauretta/dashboards"
CONFIG_FILE="$DASHBOARD_DIR/config.json"
TEMP_DIR="/tmp/dashboard-import"

# Check if dashboard directory exists
if [ ! -d "$DASHBOARD_DIR" ]; then
    echo "Dashboard directory not found: $DASHBOARD_DIR"
    exit 0
fi

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "⚠️  Config file not found: $CONFIG_FILE"
    echo "   Please copy config.example.json to config.json"
    exit 0
fi

# Create temp directory
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"

echo "Reading dashboard configuration from config.json..."

# Use Python to process dashboards
python3 << 'PYTHON_SCRIPT'
import json
import os
import sys
import subprocess
import shutil
import zipfile
import yaml

config_file = "/app/lauretta/dashboards/config.json"
dashboard_dir = "/app/lauretta/dashboards"
temp_base_dir = "/tmp/dashboard-import"

def build_sqlalchemy_uri(conn):
    # Build URI from connection fields
    return f"postgresql+psycopg2://{conn['username']}:{conn['password']}@{conn['host']}:{conn['port']}/{conn['db']}"

def count_yaml_files_in_zip(zip_path):
    """Quickly count YAML files in ZIP without extracting"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            yaml_count = sum(1 for f in zip_ref.namelist() 
                           if '/databases/' in f and (f.endswith('.yaml') or f.endswith('.yml')))
        return yaml_count
    except:
        return 0

def inject_passwords_into_zip(zip_path, connections):
    """Extract ZIP, update sqlalchemy_uri, rezip (optimized)"""
    temp_dir = os.path.join(temp_base_dir, os.path.basename(zip_path).replace('.zip', ''))
    os.makedirs(temp_dir, exist_ok=True)
    try:
        # Extract ZIP
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # Find databases directory (early exit if not found)
        databases_dir = None
        for root, dirs, _ in os.walk(temp_dir):
            if 'databases' in dirs:
                databases_dir = os.path.join(root, 'databases')
                break

        if not databases_dir:
            print(f"  ✗ databases/ directory not found")
            return None

        # Get YAML files
        yaml_files = sorted([f for f in os.listdir(databases_dir) 
                           if f.endswith(('.yaml', '.yml'))])

        if not yaml_files:
            print(f"  ✗ No YAML files found")
            return None

        # Process each connection (no verbose logging)
        for idx, conn in enumerate(connections):
            yaml_path = os.path.join(databases_dir, yaml_files[idx])
            with open(yaml_path, 'r') as f:
                data = yaml.safe_load(f)

            if not data or not isinstance(data, dict):
                print(f"  ✗ Invalid YAML: {yaml_files[idx]}")
                return None

            # Update only sqlalchemy_uri
            data['sqlalchemy_uri'] = build_sqlalchemy_uri(conn)

            with open(yaml_path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        # Create new ZIP with compression
        output_zip = os.path.join(temp_base_dir, f"modified_{os.path.basename(zip_path)}")
        with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zipf.write(file_path, arcname)

        return output_zip
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None

try:
    with open(config_file, 'r') as f:
        config = json.load(f)
    dashboards = config.get('dashboards', [])
    if not dashboards:
        print("⚠️  No dashboards configured in config.json")
        sys.exit(0)
    print(f"Found {len(dashboards)} dashboard(s) to import\n")
    success_count = 0
    fail_count = 0
    for dashboard in dashboards:
        dashboard_path = dashboard.get('path', '')
        connections = dashboard.get('connections', [])

        # Auto-add /app/ prefix for paths starting with /lauretta/
        if dashboard_path.startswith('/lauretta/'):
            dashboard_path = '/app' + dashboard_path

        if not dashboard_path:
            print("⚠️  Skipping dashboard with no path")
            continue

        if not os.path.exists(dashboard_path):
            print(f"✗ Dashboard not found: {dashboard_path}")
            fail_count += 1
            continue

        print(f"\nProcessing: {os.path.basename(dashboard_path)}")

        # Always validate connection count first (even if connections is empty)
        yaml_count = count_yaml_files_in_zip(dashboard_path)
        conn_count = len(connections)

        if conn_count != yaml_count:
            print(f"✗ ERROR: Connection count mismatch!")
            print(f"  Config has {conn_count} connections but ZIP has {yaml_count} database YAML files")
            print(f"  Both counts must match exactly")
            fail_count += 1
            continue

        # Process connections if any
        if connections:
            # Process connections
            modified_zip = inject_passwords_into_zip(dashboard_path, connections)
            if not modified_zip:
                print(f"✗ Failed to inject connections")
                fail_count += 1
                continue
            import_path = modified_zip
        else:
            import_path = dashboard_path

        # Import dashboard
        result = subprocess.run(
            ['superset', 'import-dashboards', '-p', import_path, '-u', 'admin'],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode == 0:
            print(f"✓ Successfully imported")
            success_count += 1
        else:
            print(f"✗ Import failed")
            if result.stderr:
                error_lines = [line for line in result.stderr.split('\n') 
                              if 'Loaded your LOCAL configuration' not in line 
                              and 'Setting database isolation level' not in line
                              and line.strip()]
                if error_lines:
                    for line in error_lines[:5]:  # Show only first 5 lines
                        print(f"  {line}")
            fail_count += 1
    # Cleanup
    shutil.rmtree(temp_base_dir, ignore_errors=True)

    print("\n######################################################################")
    print(f"Import completed: {success_count} success, {fail_count} failed")
    print("######################################################################")
except FileNotFoundError:
    print(f"✗ Config file not found: {config_file}")
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"✗ Invalid JSON in config file: {e}")
    sys.exit(1)
except Exception as e:
    print(f"✗ Unexpected error: {e}")
    sys.exit(1)
PYTHON_SCRIPT