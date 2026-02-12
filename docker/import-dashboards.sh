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
import re

config_file = "/app/lauretta/dashboards/config.json"
dashboard_dir = "/app/lauretta/dashboards"
temp_base_dir = "/tmp/dashboard-import"

def inject_passwords_into_zip(zip_path, connections, dashboard_name):
    """Extract ZIP, inject passwords for multiple database connections, rezip"""
    
    # Create temp directory for this dashboard
    temp_dir = os.path.join(temp_base_dir, dashboard_name.replace('.zip', ''))
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Extract ZIP
        print(f"  → Extracting {dashboard_name}...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # Find databases directory (could be in subdirectory)
        databases_dir = None
        for root, dirs, files in os.walk(temp_dir):
            if 'databases' in dirs:
                databases_dir = os.path.join(root, 'databases')
                break
        
        password_injected = False
        
        if databases_dir and os.path.exists(databases_dir):
            print(f"  → Found databases directory: {databases_dir}")
            
            # List all files
            all_files = os.listdir(databases_dir)
            print(f"  → Files in databases/: {all_files}")
            
            # Process each connection
            for connection in connections:
                database_name = connection.get('database_name', '')
                password = connection.get('password', '')
                
                if not database_name or not password:
                    print(f"  ⚠️  Skipping connection with missing database_name or password")
                    continue
                
                print(f"  → Processing connection: {database_name}")
                
                # Find target database file
                target_file = None
                if f"{database_name}.yaml" in all_files:
                    target_file = f"{database_name}.yaml"
                elif f"{database_name}.yml" in all_files:
                    target_file = f"{database_name}.yml"
                elif database_name in all_files:
                    target_file = database_name
                
                if not target_file:
                    print(f"  ⚠️  Database file not found for: {database_name}")
                    # Try to find by matching content
                    yaml_files = [f for f in all_files if f.endswith('.yaml') or f.endswith('.yml')]
                    for yaml_file in yaml_files:
                        yaml_path = os.path.join(databases_dir, yaml_file)
                        try:
                            with open(yaml_path, 'r') as f:
                                content = yaml.safe_load(f)
                                if content and isinstance(content, dict):
                                    db_name = content.get('database_name', '')
                                    if db_name == database_name:
                                        target_file = yaml_file
                                        print(f"     ✓ Found by content match: {yaml_file}")
                                        break
                        except:
                            continue
                
                if not target_file:
                    print(f"  ⚠️  Could not find YAML file for {database_name}")
                    continue
                
                yaml_path = os.path.join(databases_dir, target_file)
                print(f"  → Injecting password into {target_file}...")
                
                try:
                    # Read YAML
                    with open(yaml_path, 'r') as f:
                        data = yaml.safe_load(f)
                    
                    if data and isinstance(data, dict):
                        # Add password field
                        data['password'] = password
                        print(f"     ✓ Added password field")
                        
                        # Update sqlalchemy_uri if it exists
                        if 'sqlalchemy_uri' in data:
                            uri = data['sqlalchemy_uri']
                            print(f"     → Original URI: {uri[:50]}...")
                            
                            # Replace password in URI
                            # Pattern: protocol://user:password@host:port/db
                            if '@' in uri and ':' in uri:
                                # Find the password part between : and @
                                match = re.match(r'([^:]+://[^:]+):([^@]+)(@.+)', uri)
                                if match:
                                    new_uri = f"{match.group(1)}:{password}{match.group(3)}"
                                    data['sqlalchemy_uri'] = new_uri
                                    print(f"     ✓ Updated sqlalchemy_uri with new password")
                        
                        # Write back
                        with open(yaml_path, 'w') as f:
                            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
                        
                        password_injected = True
                        print(f"  ✓ Password injected into {target_file}")
                
                except Exception as e:
                    print(f"  ⚠️  Warning: Could not process {target_file}: {e}")
                    import traceback
                    traceback.print_exc()
        else:
            print(f"  ⚠️  databases/ directory not found in ZIP")
        
        if not password_injected:
            print(f"  ⚠️  No database files found or password injection failed")
            return None
        
        # Create new ZIP with injected password
        output_zip = os.path.join(temp_base_dir, f"modified_{dashboard_name}")
        print(f"  → Creating modified ZIP...")
        
        with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zipf.write(file_path, arcname)
        
        print(f"  ✓ Modified ZIP created: {output_zip}")
        return output_zip
    
    except Exception as e:
        print(f"  ✗ Error processing ZIP: {e}")
        import traceback
        traceback.print_exc()
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
        dashboard_name = dashboard.get('name', '')
        connections = dashboard.get('connections', [])
        
        if not dashboard_name:
            print("⚠️  Skipping dashboard with no name")
            continue
        
        dashboard_path = os.path.join(dashboard_dir, dashboard_name)
        
        if not os.path.exists(dashboard_path):
            print(f"✗ Dashboard file not found: {dashboard_name}")
            fail_count += 1
            continue
        
        print(f"Importing dashboard: {dashboard_name}")
        
        # Prepare dashboard ZIP
        import_path = dashboard_path
        
        if connections and len(connections) > 0:
            print(f"  → Found {len(connections)} database connection(s)")
            for conn in connections:
                db_name = conn.get('database_name', 'unknown')
                print(f"     - {db_name}")
            
            print(f"  → Injecting passwords into ZIP...")
            modified_zip = inject_passwords_into_zip(dashboard_path, connections, dashboard_name)
            if modified_zip:
                import_path = modified_zip
            else:
                print(f"  ✗ Failed to inject passwords, skipping import...")
                fail_count += 1
                continue
        else:
            print(f"  → No connections configured, using original ZIP")
        
        # Import dashboard
        print(f"  → Importing to Superset...")
        result = subprocess.run(
            ['superset', 'import-dashboards', '-p', import_path, '-u', 'admin'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print(f"✓ Successfully imported: {dashboard_name}\n")
            success_count += 1
        else:
            print(f"✗ Failed to import: {dashboard_name}")
            # Show error details
            if result.stderr:
                # Filter out common non-error messages
                error_lines = [line for line in result.stderr.split('\n') 
                              if 'Loaded your LOCAL configuration' not in line 
                              and 'Setting database isolation level' not in line
                              and line.strip()]
                if error_lines:
                    print(f"  Error details:")
                    for line in error_lines[:10]:  # Show first 10 lines
                        print(f"    {line}")
            fail_count += 1
            print()
    
    # Cleanup
    print("  → Cleaning up temporary files...")
    shutil.rmtree(temp_base_dir, ignore_errors=True)
    
    print("######################################################################")
    print("Dashboard import completed")
    print(f"  Success: {success_count}")
    print(f"  Failed: {fail_count}")
    
    if fail_count > 0:
        print("\n⚠️  Some dashboards failed to import.")
        print("   Check the logs above for details.")
        print("   Common issues:")
        print("   1. Wrong password in config.json")
        print("   2. Database connection issues")
        print("   3. Invalid dashboard ZIP structure")
    
    print("######################################################################")

except FileNotFoundError:
    print(f"✗ Config file not found: {config_file}")
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"✗ Invalid JSON in config file: {e}")
    sys.exit(1)
except Exception as e:
    print(f"✗ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

PYTHON_SCRIPT

