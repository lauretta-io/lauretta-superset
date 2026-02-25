import json, os, zipfile, yaml, subprocess

CONFIG_PATH = "/app/lauretta/dashboards/config.json"

def update_via_superset_shell():
    if not os.path.exists(CONFIG_PATH): return
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    for dash in config.get("dashboards", []):
        zip_path = dash.get("path")
        if zip_path.startswith('/lauretta/'): zip_path = '/app' + zip_path
        
        conn_config = dash.get("connections")
        new_name = conn_config.get("database_name")
        new_uri = f"postgresql+psycopg2://{conn_config['username']}:{conn_config['password']}@{conn_config['host']}:{conn_config['port']}/{conn_config['db']}"

        # BƯỚC 1: Lấy UUID từ file ZIP
        old_db_uuid = None
        with zipfile.ZipFile(zip_path, 'r') as z:
            db_files = [f for f in z.namelist() if 'databases/' in f and f.endswith(('.yaml', '.yml'))]
            if db_files:
                with z.open(db_files[0]) as f:
                    db_data = yaml.safe_load(f)
                    old_db_uuid = db_data.get('uuid')

        if not old_db_uuid:
            print(f"❌ Cannot find UUID in ZIP: {zip_path}")
            continue

        python_code = f"""
from superset import db
from superset.models.core import Database

# Tìm database theo UUID từ ZIP
database = db.session.query(Database).filter_by(uuid='{old_db_uuid}').first()

if database:
    print(f'Updating existing database: {{database.database_name}}')
    database.database_name = '{new_name}'
    database.sqlalchemy_uri = '{new_uri}'
else:
    print(f'Database with UUID {old_db_uuid} not found. Creating new one...')
    database = Database(
        database_name='{new_name}',
        sqlalchemy_uri='{new_uri}',
        uuid='{old_db_uuid}'
    )
    db.session.add(database)

db.session.commit()
print('✅ Database sync complete.')
"""
        print(f"🔄 Updating Database UUID {old_db_uuid}...")
        res = subprocess.run(["superset", "shell"], input=python_code, text=True, capture_output=True)
        
        if res.returncode != 0:
            print(f"❌ Error running superset shell: {res.stderr}")
            continue
        else:
            print(res.stdout)

        print(f"🚀 Importing Dashboard: {zip_path}")
        subprocess.run(["superset", "import-dashboards", "-p", zip_path, "-u", "admin"])

if __name__ == "__main__":
    update_via_superset_shell()