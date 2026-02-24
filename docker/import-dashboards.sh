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

def inject_passwords_into_zip(zip_path, connection):
    """Extract ZIP, update sqlalchemy_uri, rename datasets folder, and rezip"""
    temp_dir = os.path.join(temp_base_dir, os.path.basename(zip_path).replace('.zip', ''))
    os.makedirs(temp_dir, exist_ok=True)
    try:
        # Extract ZIP
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # Find databases directory
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

        # Update database YAML file with connection info
        yaml_path = os.path.join(databases_dir, yaml_files[0])
        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)

        if not data or not isinstance(data, dict):
            print(f"  ✗ Invalid YAML: {yaml_files[0]}")
            return None

        # Update sqlalchemy_uri and database_name
        data['sqlalchemy_uri'] = build_sqlalchemy_uri(connection)
        data['database_name'] = connection['database_name']

        with open(yaml_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        # Rename YAML file to database_name
        old_yaml_filename = yaml_files[0]
        yaml_extension = os.path.splitext(old_yaml_filename)[1]  # Get .yaml or .yml
        new_yaml_filename = f"{connection['database_name']}{yaml_extension}"
        new_yaml_path = os.path.join(databases_dir, new_yaml_filename)
        
        if yaml_path != new_yaml_path:
            os.rename(yaml_path, new_yaml_path)
            print(f"  ✓ Renamed database YAML file to: {new_yaml_filename}")

        # Find and rename datasets folder
        datasets_parent = None
        old_dataset_folder_name = None
        for root, dirs, _ in os.walk(temp_dir):
            if 'datasets' in dirs:
                datasets_parent = os.path.join(root, 'datasets')
                # Get the first subfolder inside datasets
                dataset_subfolders = [d for d in os.listdir(datasets_parent)]
                if dataset_subfolders:
                    old_dataset_folder_name = dataset_subfolders[0]
                break

        if datasets_parent and old_dataset_folder_name:
            old_dataset_path = os.path.join(datasets_parent, old_dataset_folder_name)
            new_dataset_path = os.path.join(datasets_parent, connection['database_name'])
            if os.path.exists(old_dataset_path):
                os.rename(old_dataset_path, new_dataset_path)
                print(f"  ✓ Renamed dataset folder to: {connection['database_name']}")

        # Print tree structure of modified content before zipping
        print(f"\n  📁 Folder structure after edits:")
        def print_tree(directory, prefix="    ", is_last=True):
            """Print directory tree structure"""
            try:
                items = sorted(os.listdir(directory))
                dirs = [d for d in items if os.path.isdir(os.path.join(directory, d))]
                files = [f for f in items if os.path.isfile(os.path.join(directory, f))]
                
                # Print directories first
                for i, d in enumerate(dirs):
                    is_last_item = (i == len(dirs) - 1) and len(files) == 0
                    print(f"{prefix}{'└── ' if is_last_item else '├── '}{d}/")
                    extension = "    " if is_last_item else "│   "
                    print_tree(os.path.join(directory, d), prefix + extension, is_last_item)
                
                # Print files
                for i, f in enumerate(files):
                    is_last_item = (i == len(files) - 1)
                    print(f"{prefix}{'└── ' if is_last_item else '├── '}{f}")
            except PermissionError:
                pass
        
        print_tree(temp_dir)
        print()

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
        connection = dashboard.get('connections')

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

        # Validate that connection exists and has database_name
        if not connection or not isinstance(connection, dict):
            print(f"✗ ERROR: No connection configuration found")
            fail_count += 1
            continue

        if 'database_name' not in connection:
            print(f"✗ ERROR: 'database_name' field missing in connection configuration")
            fail_count += 1
            continue

        # Process connection: modify ZIP and import dashboard
        modified_zip = inject_passwords_into_zip(dashboard_path, connection)
        if not modified_zip:
            print(f"✗ Failed to process dashboard")
            fail_count += 1
            continue

        # Step 1: Set database URI
        database_name = connection.get('database_name')
        sqlalchemy_uri = build_sqlalchemy_uri(connection)
        
        print(f"  Setting database URI for: {database_name}")
        result = subprocess.run(
            ['superset', 'set-database-uri', '--database_name', database_name, '--uri', sqlalchemy_uri],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            print(f"  ✗ Failed to set database URI")
            if result.stderr:
                error_lines = [line for line in result.stderr.split('\n') if line.strip()]
                for line in error_lines[:3]:
                    print(f"    {line}")
            fail_count += 1
            continue

        print(f"  ✓ Database URI set successfully")

        # Step 2: Import dashboard
        result = subprocess.run(
            ['superset', 'import-dashboards', '-p', modified_zip, '-u', 'admin'],
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