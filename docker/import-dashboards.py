import json, os, zipfile, yaml, subprocess, uuid, shutil, re, glob, string, random

CONFIG_PATH = "/app/lauretta/dashboards/config.json"
STATE_PATH = "/app/lauretta/dashboards/state.json"

def read_all_state():
    """Read the full state dict from state.json (keyed by dashboard path)."""
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, 'r') as f:
                data = json.load(f)
                # Migrate legacy flat state (single dashboard) to keyed format
                if isinstance(data, dict) and 'zip_path' in data:
                    key = data['zip_path']
                    return {key: data}
                return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"⚠️ Error reading state.json: {e}")
    return {}

def read_state(dashboard_key):
    """Read the state for a specific dashboard (identified by its config path)."""
    all_state = read_all_state()
    return all_state.get(dashboard_key, {})

def write_state(state, dashboard_key):
    """Write the state for a specific dashboard, preserving other dashboards' state."""
    try:
        all_state = read_all_state()
        all_state[dashboard_key] = state
        with open(STATE_PATH, 'w') as f:
            json.dump(all_state, f, indent=2)
        print(f"💾 State saved for dashboard '{dashboard_key}'")
    except Exception as e:
        print(f"⚠️ Error writing state.json: {e}")

def cleanup_old_map_charts_and_datasets_from_state(dashboard_key):
    """Remove old MAP FLOOR charts and MAP datasets from the database using state.json.
    Only deletes charts/datasets that were previously created for this dashboard."""
    state = read_state(dashboard_key)
    
    old_charts = state.get('charts', [])
    old_datasets = state.get('datasets', [])
    
    if not old_charts and not old_datasets:
        print("ℹ️ No previous state found, nothing to clean up")
        return
    
    chart_uuids = [str(c.get('uuid')) for c in old_charts if c.get('uuid')]
    dataset_uuids = [str(d.get('uuid')) for d in old_datasets if d.get('uuid')]
    # Legacy fallback for old state schema
    chart_names = [c.get('slice_name') for c in old_charts if c.get('slice_name')]
    dataset_names = [d.get('table_name') for d in old_datasets if d.get('table_name')]
    
    chart_uuids_str = str(chart_uuids)
    dataset_uuids_str = str(dataset_uuids)
    chart_names_str = str(chart_names)
    dataset_names_str = str(dataset_names)
    
    python_code = f"""
from superset import db
from superset.models.slice import Slice
from superset.connectors.sqla.models import SqlaTable

chart_uuids = {chart_uuids_str}
dataset_uuids = {dataset_uuids_str}
chart_names = {chart_names_str}
dataset_names = {dataset_names_str}

# Delete MAP FLOOR charts (slices) from previous state
map_charts = dict()
if chart_uuids:
    for chart in db.session.query(Slice).filter(Slice.uuid.in_(chart_uuids)).all():
        map_charts[chart.id] = chart
if chart_names:
    for chart in db.session.query(Slice).filter(Slice.slice_name.in_(chart_names)).all():
        map_charts[chart.id] = chart

for chart in map_charts.values():
    print(f'🧹 Deleting chart: {{chart.slice_name}} (ID: {{chart.id}})')
    db.session.delete(chart)

# Delete MAP datasets from previous state
map_datasets = dict()
if dataset_uuids:
    for dataset in db.session.query(SqlaTable).filter(SqlaTable.uuid.in_(dataset_uuids)).all():
        map_datasets[dataset.id] = dataset
if dataset_names:
    for dataset in db.session.query(SqlaTable).filter(SqlaTable.table_name.in_(dataset_names)).all():
        map_datasets[dataset.id] = dataset

for dataset in map_datasets.values():
    print(f'🧹 Deleting dataset: {{dataset.table_name}} (ID: {{dataset.id}})')
    db.session.delete(dataset)

db.session.commit()
print(f'✅ Cleaned up {{len(map_charts)}} charts and {{len(map_datasets)}} datasets')
"""
    print(
        f"🧹 Cleaning up old items from state.json: "
        f"{len(chart_uuids)} chart uuids ({len(chart_names)} names fallback), "
        f"{len(dataset_uuids)} dataset uuids ({len(dataset_names)} names fallback)..."
    )
    res = subprocess.run(["superset", "shell"], input=python_code, text=True, capture_output=True)
    if res.returncode != 0:
        print(f"⚠️ Warning during cleanup: {res.stderr}")
    else:
        print(res.stdout)


def generate_uuid():
    """Generate a random UUID string."""
    return str(uuid.uuid4())

def generate_database_uuid(dashboard_path, conn_config):
    """Generate a stable database UUID per dashboard path."""
    dashboard_key = str(dashboard_path or '')
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lauretta-db::{dashboard_key}"))

def generate_chart_id(length=20):
    """Generate a random chart ID like 'CHART-Wzo9LQ6k0cJGV-jcdmk4n'."""
    chars = string.ascii_letters + string.digits + '-_'
    return ''.join(random.choice(chars) for _ in range(length))

def find_extract_dir(zip_path):
    """Find or create the extraction directory for a zip file.
    Always re-extract to ensure clean state (avoid stale data from previous runs).
    """
    base_dir = os.path.dirname(zip_path)
    
    # Remove existing dashboard_export_* directories to start fresh
    existing = glob.glob(os.path.join(base_dir, 'dashboard_export_*'))
    for dir_path in existing:
        shutil.rmtree(dir_path)
        print(f"🧹 Removed old extract directory: {os.path.basename(dir_path)}")
    
    # Extract fresh from ZIP
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(base_dir)
    
    existing = glob.glob(os.path.join(base_dir, 'dashboard_export_*'))
    return sorted(existing)[-1] if existing else None

def find_template_dataset(extract_dir):
    """Find a MAP_*.yaml dataset file to use as template."""
    datasets_dir = os.path.join(extract_dir, 'datasets')
    for root, dirs, files in os.walk(datasets_dir):
        for f in files:
            if f.startswith('MAP_') and f.endswith('.yaml'):
                return os.path.join(root, f)
    return None

def create_default_dataset_template(floor_id, floor_name, db_uuid):
    """Create a default MAP dataset template structure when no template exists in ZIP."""
    sql_template = """{% set is_hourly = false %}
{% set from_str = from_dttm | string if from_dttm else '' %}
{% set to_str = to_dttm | string if to_dttm else '' %}
{% if (from_str and '00:00:00' not in from_str) or (to_str and '00:00:00' not in to_str) or (from_str[:10] == to_str[:10]) %}
    {% set is_hourly = true %}
{% endif %}
{% set suffix = '_hourly' if is_hourly else '_daily' %}
{% set t_col = 'timestamp' if is_hourly else 'datestamp' %}

{% set start_date = from_dttm if from_dttm else "CURRENT_DATE - INTERVAL '1 day'" %}
{% set end_date = to_dttm if to_dttm else "CURRENT_DATE" %}

SELECT 
    res.floor_id,
    res.zone_name,
    res.name,
    res.category,
    res.points,
    res.total_footfall_zo,
    COALESCE(res.event_time, CAST({{ "'" + start_date + "'" if from_dttm else start_date }} AS TIMESTAMP)) AS event_time
FROM (
    -- Units
    SELECT
        z.floor_id, z.name as zone_name, u.name AS name, ug.name AS category, z.points,
        COALESCE(usd.total_footfall_zo, 0) AS total_footfall_zo,
        usd.event_time
    FROM property.zones z
    LEFT JOIN property.unit_zone_mappings uzm ON uzm.zone_id = z.id
    LEFT JOIN property.units u ON u.id = uzm.unit_id
    LEFT JOIN property.unit_unit_group_mappings uugm ON uugm.unit_id = u.id
    LEFT JOIN property.unit_groups ug ON ug.id = uugm.unit_group_id
    LEFT JOIN (
        SELECT unit_id, SUM(footfall_zo) AS total_footfall_zo, MAX({{ t_col }}) as event_time
        FROM property.unit_summary{{ suffix }}
        WHERE 1=1
          {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
          {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
        GROUP BY unit_id
    ) usd ON usd.unit_id = u.id
    WHERE z.floor_id = {FLOOR_ID}
      {% if filter_values('unit_name') %} AND u.name IN ({{ "'" + filter_values('unit_name') | join("','") + "'" }}) {% endif %}
      {% if filter_values('unit_group_name') %} AND ug.name IN ({{ "'" + filter_values('unit_group_name') | join("','") + "'" }}) {% endif %}

    UNION ALL

    -- Public Spaces
    SELECT
        z.floor_id, z.name as zone_name, ps.name as name, 'Public' AS category, z.points,
        COALESCE(pssd.total_footfall_zo, 0) AS total_footfall_zo,
        pssd.event_time
    FROM property.public_spaces ps 
    LEFT JOIN property.public_space_zone_mappings pszm ON pszm.public_space_id = ps.id 
    LEFT JOIN property.zones z ON z.id = pszm.zone_id 
    LEFT JOIN (
        SELECT public_space_id, SUM(footfall_zo) AS total_footfall_zo, MAX({{ t_col }}) as event_time
        FROM property.public_space_summary{{ suffix }}
        WHERE 1=1
          {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
          {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
        GROUP BY public_space_id
    ) pssd ON pssd.public_space_id = ps.id
    WHERE ps.floor_id = {FLOOR_ID}

    UNION ALL

    -- Entrances
    SELECT
        z.floor_id, z.name as zone_name, e.name as name, 'Entrances' AS category, z.points,
        COALESCE(esd.total_footfall_zo, 0) AS total_footfall_zo,
        esd.event_time
    FROM property.entrances e
    LEFT JOIN property.entrance_zone_mappings ezm ON ezm.entrance_id = e.id
    LEFT JOIN property.zones z ON z.id = ezm.zone_id
    LEFT JOIN (
        SELECT entrance_id, SUM(footfall_zo) AS total_footfall_zo, MAX({{ t_col }}) as event_time
        FROM property.entrance_summary{{ suffix }}
        WHERE 1=1
          {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
          {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
        GROUP BY entrance_id
    ) esd ON esd.entrance_id = e.id
    WHERE e.floor_id = {FLOOR_ID}

    UNION ALL

    -- Escalators
    SELECT
        z.floor_id, z.name as zone_name, e.name as name, 'Circulation' AS category, z.points,
        COALESCE(esd.total_footfall_zo, 0) AS total_footfall_zo,
        esd.event_time
    FROM property.escalators e
    LEFT JOIN property.escalator_zone_mappings ezm ON ezm.escalator_id = e.id
    LEFT JOIN property.zones z ON z.id = ezm.zone_id
    LEFT JOIN (
        SELECT escalator_id, SUM(footfall_zo) AS total_footfall_zo, MAX({{ t_col }}) as event_time
        FROM property.escalator_summary{{ suffix }}
        WHERE 1=1
          {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
          {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
        GROUP BY escalator_id
    ) esd ON esd.escalator_id = e.id
    WHERE z.floor_id = {FLOOR_ID}

    UNION ALL

    -- Lift Lobbies
    SELECT
        z.floor_id, z.name as zone_name, ll.name as name, 'Circulation' AS category, z.points,
        COALESCE(llsd.total_footfall_zo, 0) AS total_footfall_zo,
        llsd.event_time
    FROM property.lift_lobbies ll
    LEFT JOIN property.lift_lobby_zone_mappings llzm ON llzm.lift_lobby_id = ll.id
    LEFT JOIN property.zones z ON z.id = llzm.zone_id
    LEFT JOIN (
        SELECT lift_lobby_id, SUM(footfall_zo) AS total_footfall_zo, MAX({{ t_col }}) as event_time
        FROM property.lift_lobby_summary{{ suffix }}
        WHERE 1=1
          {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
          {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
        GROUP BY lift_lobby_id
    ) llsd ON llsd.lift_lobby_id = ll.id
    WHERE ll.floor_id = {FLOOR_ID}

) res WHERE res.name IS NOT NULL AND res.category IS NOT NULL"""

    # Replace floor_id placeholder
    sql = sql_template.replace('{FLOOR_ID}', str(floor_id))
    
    return {
        'table_name': f'MAP {floor_name}',
        'main_dttm_col': None,
        'description': None,
        'default_endpoint': None,
        'offset': 0,
        'cache_timeout': None,
        'catalog': 'property',
        'schema': 'property',
        'sql': sql,
        'params': None,
        'template_params': None,
        'filter_select_enabled': True,
        'fetch_values_predicate': None,
        'extra': None,
        'normalize_columns': False,
        'always_filter_main_dttm': False,
        'uuid': generate_uuid(),
        'metrics': [
            {
                'metric_name': 'count',
                'verbose_name': 'COUNT(*)',
                'metric_type': 'count',
                'expression': 'COUNT(*)',
                'description': None,
                'd3format': None,
                'currency': None,
                'extra': {'warning_markdown': ''},
                'warning_text': None
            }
        ],
        'columns': [
            {
                'column_name': 'event_time',
                'verbose_name': None,
                'is_dttm': True,
                'is_active': True,
                'type': 'DATETIME',
                'advanced_data_type': None,
                'groupby': True,
                'filterable': True,
                'expression': None,
                'description': None,
                'python_date_format': None,
                'extra': {}
            },
            {
                'column_name': 'total_footfall_zo',
                'verbose_name': None,
                'is_dttm': False,
                'is_active': True,
                'type': 'LONGINTEGER',
                'advanced_data_type': None,
                'groupby': True,
                'filterable': True,
                'expression': None,
                'description': None,
                'python_date_format': None,
                'extra': {}
            },
            {
                'column_name': 'floor_id',
                'verbose_name': None,
                'is_dttm': False,
                'is_active': True,
                'type': 'INTEGER',
                'advanced_data_type': None,
                'groupby': True,
                'filterable': True,
                'expression': None,
                'description': None,
                'python_date_format': None,
                'extra': {}
            },
            {
                'column_name': 'zone_name',
                'verbose_name': None,
                'is_dttm': False,
                'is_active': True,
                'type': 'STRING',
                'advanced_data_type': None,
                'groupby': True,
                'filterable': True,
                'expression': None,
                'description': None,
                'python_date_format': None,
                'extra': {}
            },
            {
                'column_name': 'category',
                'verbose_name': None,
                'is_dttm': False,
                'is_active': True,
                'type': 'STRING',
                'advanced_data_type': None,
                'groupby': True,
                'filterable': True,
                'expression': None,
                'description': None,
                'python_date_format': None,
                'extra': {}
            },
            {
                'column_name': 'name',
                'verbose_name': None,
                'is_dttm': False,
                'is_active': True,
                'type': 'STRING',
                'advanced_data_type': None,
                'groupby': True,
                'filterable': True,
                'expression': None,
                'description': None,
                'python_date_format': None,
                'extra': {}
            },
            {
                'column_name': 'points',
                'verbose_name': None,
                'is_dttm': False,
                'is_active': True,
                'type': 'STRING',
                'advanced_data_type': None,
                'groupby': True,
                'filterable': True,
                'expression': None,
                'description': None,
                'python_date_format': None,
                'extra': {}
            }
        ],
        'version': '1.0.0',
        'database_uuid': db_uuid
    }

def find_template_chart(extract_dir):
    """Find a MAP_FLOOR_*.yaml chart file to use as template."""
    charts_dir = os.path.join(extract_dir, 'charts')
    for f in os.listdir(charts_dir):
        if f.startswith('MAP_FLOOR_') and f.endswith('.yaml'):
            return os.path.join(charts_dir, f)
    return None


def build_public_floor_image_url(floor_image):
    """Convert floor image references to a public Lauretta image URL."""
    image_ref = str(floor_image or '').strip()
    if not image_ref:
        return ''
    if image_ref.startswith(('http://', 'https://', '/')):
        return image_ref
    cleaned_ref = image_ref.lstrip('/')
    if cleaned_ref.startswith('api/v1/lauretta/images/'):
        return f'/{cleaned_ref}'
    return f'/api/v1/lauretta/images/{cleaned_ref}'

def create_default_chart_template(floor_name, chart_id, dataset_uuid, floor_image=''):
    """Create a default MAP FLOOR chart template structure when no template exists in ZIP."""
    public_floor_image = build_public_floor_image_url(floor_image)
    locked_floor_image = f'locked:{public_floor_image}' if public_floor_image else ''

    params = {
        'viz_type': 'ext-floor-map',
        'slice_id': chart_id,
        'floor_selection': floor_name,
        'floor_image': locked_floor_image,
        'floor_image_locked': True,
        'cols': ['name', 'category', 'points', 'total_footfall_zo'],
        'adhoc_filters': [
            {
                'clause': 'WHERE',
                'comparator': 'Last day',
                'datasourceWarning': False,
                'expressionType': 'SIMPLE',
                'filterOptionName': f'filter_{generate_chart_id(10)}',
                'isExtra': False,
                'isNew': False,
                'operator': 'TEMPORAL_RANGE',
                'sqlExpression': None,
                'subject': 'datestamp'
            }
        ],
        'row_limit': 5000,
        'extra_form_data': {}
    }
    
    query_context = {
        'datasource': {'type': 'table'},
        'force': False,
        'queries': [
            {
                'filters': [{'col': 'event_time', 'op': 'TEMPORAL_RANGE', 'val': 'Last day'}],
                'extras': {'having': '', 'where': ''},
                'applied_time_extras': {},
                'columns': [],
                'metrics': [{'expressionType': 'SQL', 'sqlExpression': 'COUNT(*)', 'label': '_dummy_metric'}],
                'annotation_layers': [],
                'row_limit': 5000,
                'series_limit': 0,
                'order_desc': True,
                'url_params': {},
                'custom_params': {},
                'custom_form_data': {},
                'groupby': ['name', 'category', 'points', 'total_footfall_zo']
            }
        ],
        'form_data': {
            'viz_type': 'ext-floor-map',
            'slice_id': chart_id,
            'floor_selection': floor_name,
            'floor_image': locked_floor_image,
            'floor_image_locked': True,
            'cols': ['name', 'category', 'points', 'total_footfall_zo'],
            'adhoc_filters': params['adhoc_filters'],
            'row_limit': 5000,
            'extra_form_data': {},
            'force': False,
            'result_format': 'json',
            'result_type': 'full'
        },
        'result_format': 'json',
        'result_type': 'full'
    }
    
    return {
        'slice_name': f'MAP FLOOR {floor_name}',
        'description': None,
        'certified_by': None,
        'certification_details': None,
        'viz_type': 'ext-floor-map',
        'params': params,
        'query_context': json.dumps(query_context),
        'cache_timeout': None,
        'uuid': generate_uuid(),
        'version': '1.0.0',
        'dataset_uuid': dataset_uuid
    }

def find_dashboard_file(extract_dir):
    """Find the dashboard YAML file."""
    dashboards_dir = os.path.join(extract_dir, 'dashboards')
    for f in os.listdir(dashboards_dir):
        if f.endswith('.yaml'):
            return os.path.join(dashboards_dir, f)
    return None

def replace_floor_id_in_sql(sql, old_floor_id, new_floor_id):
    """Replace floor_id references in SQL query."""
    # Replace patterns like: WHERE z.floor_id = 3, WHERE ll.floor_id = 3, etc.
    patterns = [
        (rf'(\.floor_id\s*=\s*){old_floor_id}(\D|$)', rf'\g<1>{new_floor_id}\g<2>'),
    ]
    result = sql
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)
    return result

def generate_floor_datasets(extract_dir, floors, db_uuid):
    """Generate dataset YAML files for each floor."""
    # Always generate datasets from built-in template (do not depend on ZIP templates)
    datasets_dir = os.path.join(extract_dir, 'datasets', 'None')
    os.makedirs(datasets_dir, exist_ok=True)
    
    # Remove ALL existing MAP_*.yaml files including the template
    if os.path.exists(datasets_dir):
        for f in os.listdir(datasets_dir):
            if f.startswith('MAP_') and f.endswith('.yaml'):
                old_file = os.path.join(datasets_dir, f)
                os.remove(old_file)
                print(f"🧹 Removed dataset: {f}")
    
    created_datasets = []
    
    for floor in floors:
        floor_id = floor['id']
        floor_name = floor['name']

        # Generate dataset from scratch using built-in template
        new_dataset = create_default_dataset_template(floor_id, floor_name, db_uuid)
        
        # Write dataset file
        filename = f"MAP_{floor_name}.yaml"
        filepath = os.path.join(datasets_dir, filename)
        
        with open(filepath, 'w') as f:
            yaml.dump(new_dataset, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        created_datasets.append({
            'floor_id': floor_id,
            'floor_name': floor_name,
            'uuid': new_dataset['uuid'],
            'table_name': new_dataset['table_name'],
            'filename': filename
        })
        
        print(f"📊 Created dataset: {filename} (UUID: {new_dataset['uuid'][:8]}...)")
    
    return created_datasets

def generate_floor_charts(extract_dir, floors, created_datasets, starting_chart_id=100):
    """Generate chart YAML files for each floor."""
    # Always generate charts from built-in template (do not depend on ZIP templates)
    
    charts_dir = os.path.join(extract_dir, 'charts')
    os.makedirs(charts_dir, exist_ok=True)
    
    # Remove ALL existing MAP_FLOOR_*.yaml files including the template
    if os.path.exists(charts_dir):
        for f in os.listdir(charts_dir):
            if f.startswith('MAP_FLOOR_') and f.endswith('.yaml'):
                old_file = os.path.join(charts_dir, f)
                os.remove(old_file)
                print(f"🧹 Removed chart: {f}")
    
    created_charts = []
    chart_id = starting_chart_id
    
    for floor in floors:
        floor_name = floor['name']
        
        # Find matching dataset
        dataset = next((d for d in created_datasets if d['floor_name'] == floor_name), None)
        if not dataset:
            print(f"⚠️ No dataset found for floor {floor_name}")
            continue
        
        # Generate chart from scratch using built-in template function
        new_chart = create_default_chart_template(
            floor_name,
            chart_id,
            dataset['uuid'],
            floor.get('image', ''),
        )
        
        # Write chart file
        filename = f"MAP_FLOOR_{floor_name}_{chart_id}.yaml"
        filepath = os.path.join(charts_dir, filename)
        
        with open(filepath, 'w') as f:
            yaml.dump(new_chart, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        created_charts.append({
            'floor_name': floor_name,
            'uuid': new_chart['uuid'],
            'slice_name': new_chart['slice_name'],
            'chart_id': chart_id,
            'filename': filename,
            'component_id': f"CHART-{generate_chart_id()}"
        })
        
        print(f"📈 Created chart: {filename} (UUID: {new_chart['uuid'][:8]}...)")
        chart_id += 1
    
    return created_charts

def update_dashboard_with_charts(extract_dir, created_charts):
    """Update dashboard YAML to include the new floor map charts."""
    dashboard_path = find_dashboard_file(extract_dir)
    if not dashboard_path:
        print("⚠️ No dashboard YAML found")
        return
    
    with open(dashboard_path, 'r') as f:
        dashboard = yaml.safe_load(f)
    
    position = dashboard.get('position', {})
    
    # Resolve ROOT / GRID IDs from position
    root_id = 'ROOT_ID' if 'ROOT_ID' in position else None
    grid_id = 'GRID_ID' if 'GRID_ID' in position else None

    if not root_id:
        for key, value in position.items():
            if isinstance(value, dict) and value.get('type') == 'ROOT':
                root_id = key
                break

    if not grid_id:
        for key, value in position.items():
            if isinstance(value, dict) and value.get('type') == 'GRID':
                grid_id = key
                break

    if not root_id or not grid_id:
        print("⚠️ Could not resolve ROOT/GRID in dashboard layout")
        return

    # Find a top-level tabs container under GRID (or create one)
    tabs_container_id = None
    grid_children = position.get(grid_id, {}).get('children', [])

    for child_id in grid_children:
        child = position.get(child_id, {})
        if isinstance(child, dict) and child.get('type') == 'TABS':
            tabs_container_id = child_id
            break

    if not tabs_container_id:
        for key, value in position.items():
            if (
                isinstance(value, dict)
                and value.get('type') == 'TABS'
                and value.get('parents', [])
                and value.get('parents', [])[-1] == grid_id
            ):
                tabs_container_id = key
                break

    if not tabs_container_id:
        tabs_container_id = f"TABS-{generate_chart_id()}"
        position[tabs_container_id] = {
            'children': [],
            'id': tabs_container_id,
            'meta': {},
            'parents': [root_id, grid_id],
            'type': 'TABS'
        }
        if grid_id in position:
            if 'children' not in position[grid_id]:
                position[grid_id]['children'] = []
            position[grid_id]['children'].append(tabs_container_id)
        print(f"📁 Created tabs container: {tabs_container_id}")

    tabs_parents = position.get(tabs_container_id, {}).get('parents', [root_id, grid_id])

    # Create a fresh Map View tab
    map_tab_id = f"TAB-{generate_chart_id()}"
    map_tab_parents = tabs_parents + [tabs_container_id]
    position[map_tab_id] = {
        'children': [],
        'id': map_tab_id,
        'meta': {'text': 'Map View'},
        'parents': map_tab_parents,
        'type': 'TAB'
    }
    if tabs_container_id in position:
        if 'children' not in position[tabs_container_id]:
            position[tabs_container_id]['children'] = []
        position[tabs_container_id]['children'].append(map_tab_id)
    print(f"🆕 Created Map View tab: {map_tab_id}")

    # Determine the hierarchy path to Map View tab
    base_parents = map_tab_parents + [map_tab_id]
    
    # Constants for layout
    MAX_CHARTS_PER_ROW = 3
    
    # Create rows and add charts (max 3 per row)
    new_chart_ids = []
    created_rows = []
    
    for i, chart in enumerate(created_charts):
        row_index = i // MAX_CHARTS_PER_ROW
        
        # Create new row if needed
        if row_index >= len(created_rows):
            new_row_id = f"ROW-{generate_chart_id()}"
            position[new_row_id] = {
                'children': [],
                'id': new_row_id,
                'meta': {'background': 'BACKGROUND_TRANSPARENT'},
                'parents': base_parents.copy(),
                'type': 'ROW'
            }
            created_rows.append(new_row_id)
            
            # Add row to tab's children
            if map_tab_id in position:
                if 'children' not in position[map_tab_id]:
                    position[map_tab_id]['children'] = []
                position[map_tab_id]['children'].append(new_row_id)
            print(f"📐 Created row {row_index + 1}: {new_row_id}")
        
        # Get current row for this chart
        current_row_id = created_rows[row_index]
        component_id = chart['component_id']
        
        # Add chart position entry with correct parents including the row
        chart_parents = base_parents + [current_row_id]
        position[component_id] = {
            'children': [],
            'id': component_id,
            'meta': {
                'chartId': chart['chart_id'],
                'height': 50,
                'sliceName': chart['slice_name'],
                'uuid': chart['uuid'],
                'width': 4
            },
            'parents': chart_parents,
            'type': 'CHART'
        }
        
        # Add to row children
        position[current_row_id]['children'].append(component_id)
        
        new_chart_ids.append(chart['chart_id'])
        print(f"🗺️ Added chart to row {row_index + 1}: {chart['slice_name']}")
    
    # Update metadata chartsInScope arrays
    metadata = dashboard.get('metadata', {})
    
    # Helper to clean stale chart IDs and add new ones
    # Remove: old generated chart IDs (>= 100) AND the original template chart ID (26)
    def update_charts_in_scope(charts_list, new_ids, old_template_id=26):
        # Remove old generated chart IDs (>= 100) and the original template (26)
        cleaned = [c for c in charts_list if c < 100 and c != old_template_id]
        # Add new chart IDs
        for chart_id in new_ids:
            if chart_id not in cleaned:
                cleaned.append(chart_id)
        return cleaned
    
    # Update global_chart_configuration.chartsInScope
    if 'global_chart_configuration' in metadata:
        charts_in_scope = metadata['global_chart_configuration'].get('chartsInScope', [])
        metadata['global_chart_configuration']['chartsInScope'] = update_charts_in_scope(charts_in_scope, new_chart_ids)
    
    # Update chart_configuration cross filters
    if 'chart_configuration' in metadata:
        for key, config in metadata['chart_configuration'].items():
            if 'crossFilters' in config:
                charts_in_scope = config['crossFilters'].get('chartsInScope', [])
                config['crossFilters']['chartsInScope'] = update_charts_in_scope(charts_in_scope, new_chart_ids)
    
    # Update native_filter_configuration for specific filters to include new Map View tab
    filter_names_to_expand = {'time_range', 'categories', 'stores'}

    if 'native_filter_configuration' in metadata:
        for filter_config in metadata['native_filter_configuration']:
            filter_name = str(filter_config.get('name', '')).strip().lower()
            if filter_name not in filter_names_to_expand:
                continue

            # Expand scope.rootPath to include the new Map View tab
            scope = filter_config.setdefault('scope', {})
            root_path = scope.get('rootPath', [])
            if not isinstance(root_path, list):
                root_path = []
            if map_tab_id not in root_path:
                root_path.append(map_tab_id)
            scope['rootPath'] = root_path

            # Ensure tab is not excluded
            excluded = scope.get('excluded', [])
            if isinstance(excluded, list):
                scope['excluded'] = [item for item in excluded if item != map_tab_id]
            else:
                scope['excluded'] = []

            # Expand tabsInScope to include the new Map View tab
            tabs_in_scope = filter_config.get('tabsInScope', [])
            if not isinstance(tabs_in_scope, list):
                tabs_in_scope = []
            if map_tab_id not in tabs_in_scope:
                tabs_in_scope.append(map_tab_id)
            filter_config['tabsInScope'] = tabs_in_scope

            # Expand chartsInScope to include new generated map charts
            charts_in_scope = filter_config.get('chartsInScope', [])
            if not isinstance(charts_in_scope, list):
                charts_in_scope = []
            filter_config['chartsInScope'] = update_charts_in_scope(
                charts_in_scope, new_chart_ids
            )
    
    dashboard['position'] = position
    dashboard['metadata'] = metadata
    
    # Write updated dashboard
    with open(dashboard_path, 'w') as f:
        yaml.dump(dashboard, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    print(f"✅ Dashboard updated with {len(created_charts)} new floor map charts")

def update_database_yaml_credentials(extract_dir, conn_config, db_display_name, target_db_uuid):
    """Rewrite databases/*.yaml inside the extracted ZIP with real credentials
    from config.json so the Superset importer won't reject the masked password."""
    databases_dir = os.path.join(extract_dir, 'databases')
    if not os.path.isdir(databases_dir):
        print("⚠️ No databases/ folder in extracted ZIP")
        return

    new_uri = (
        f"postgresql+psycopg2://{conn_config['username']}:{conn_config['password']}"
        f"@{conn_config['host']}:{conn_config['port']}/{conn_config['db']}"
    )

    for fname in os.listdir(databases_dir):
        if not fname.endswith(('.yaml', '.yml')):
            continue
        fpath = os.path.join(databases_dir, fname)
        with open(fpath, 'r') as f:
            db_data = yaml.safe_load(f)
        if not isinstance(db_data, dict):
            continue
        db_data['sqlalchemy_uri'] = new_uri
        db_data['database_name'] = db_display_name
        db_data['uuid'] = target_db_uuid
        with open(fpath, 'w') as f:
            yaml.dump(db_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"🔐 Updated database credentials in {fname}")

def update_dataset_database_uuid(extract_dir, target_db_uuid):
    """Rewrite datasets/*.yaml database_uuid to match the target database UUID."""
    datasets_dir = os.path.join(extract_dir, 'datasets')
    if not os.path.isdir(datasets_dir):
        print("⚠️ No datasets/ folder in extracted ZIP")
        return

    updated = 0
    for root, _, files in os.walk(datasets_dir):
        for fname in files:
            if not fname.endswith(('.yaml', '.yml')):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, 'r') as f:
                ds_data = yaml.safe_load(f)
            if not isinstance(ds_data, dict):
                continue
            ds_data['database_uuid'] = target_db_uuid
            with open(fpath, 'w') as f:
                yaml.dump(ds_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            updated += 1
    print(f"🔗 Updated database_uuid for {updated} dataset files")


def process_floor_maps(zip_path, floors, db_uuid, conn_config=None, db_display_name=None, starting_chart_id=100):
    """Process floor maps: generate datasets, charts, and update dashboard.
    Returns tuple: (new_zip_path, created_datasets, created_charts)"""
    if not floors:
        print("ℹ️ No floors configured, skipping floor map generation")
        return None, [], []
    # Find extraction directory
    extract_dir = find_extract_dir(zip_path)
    if not extract_dir:
        print("❌ Could not find or create extraction directory")
        return None, [], []
    print(f"📂 Processing floor maps in: {extract_dir}")
    # Generate datasets
    created_datasets = generate_floor_datasets(extract_dir, floors, db_uuid)
    # Generate charts
    created_charts = generate_floor_charts(extract_dir, floors, created_datasets, starting_chart_id=starting_chart_id)
    # Update dashboard
    update_dashboard_with_charts(extract_dir, created_charts)
    # Inject real database credentials into the ZIP before import
    if conn_config and db_display_name:
        update_database_yaml_credentials(extract_dir, conn_config, db_display_name, db_uuid)
    update_dataset_database_uuid(extract_dir, db_uuid)
    # Re-zip the modified dashboard export
    new_zip_path = rezip_dashboard_export(extract_dir, zip_path)
    return new_zip_path, created_datasets, created_charts

def rezip_dashboard_export(extract_dir, zip_path):
    """Re-create the zip file with the modified contents, never overwrite the original."""
    # Create new zip file with .imported.zip suffix
    new_zip_path = zip_path.replace('.zip', '.imported.zip')
    
    with zipfile.ZipFile(new_zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.dirname(extract_dir))
                # Read file content and create ZipInfo with valid timestamp
                # (ZIP doesn't support timestamps before 1980)
                with open(file_path, 'rb') as f:
                    data = f.read()
                info = zipfile.ZipInfo(arcname, date_time=(2026, 3, 4, 12, 0, 0))
                z.writestr(info, data)
    print(f"📦 Created dashboard import zip: {new_zip_path}")
    return new_zip_path

def update_via_superset_shell():
    if not os.path.exists(CONFIG_PATH): return
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    for dash_index, dash in enumerate(config.get("dashboards", [])):
        zip_path = dash.get("path")
        if zip_path.startswith('/lauretta/'): zip_path = '/app' + zip_path
        print(f"\n{'='*60}")
        print(f"📋 Dashboard {dash_index + 1}/{len(config['dashboards'])}: {dash.get('path')}")
        print(f"{'='*60}")
        conn_config = dash.get("connections")
        if not isinstance(conn_config, dict):
            print(f"⚠️ Missing or invalid connections config for dashboard: {dash.get('path')}")
            continue
        new_name = conn_config.get("database_display_name", conn_config.get("database_name", "Database"))
        new_uri = f"postgresql+psycopg2://{conn_config['username']}:{conn_config['password']}@{conn_config['host']}:{conn_config['port']}/{conn_config['db']}"
        target_db_uuid = generate_database_uuid(dash.get("path"), conn_config)
        print(f"\n🔍 NEW URI: {new_uri}")
        print(f"🧩 TARGET DB UUID: {target_db_uuid}")
        # Step 1: Trace UUID from file ZIP
        source_db_uuid = None
        with zipfile.ZipFile(zip_path, 'r') as z:
            db_files = [f for f in z.namelist() if 'databases/' in f and f.endswith(('.yaml', '.yml'))]
            if db_files:
                with z.open(db_files[0]) as f:
                    db_data = yaml.safe_load(f)
                    source_db_uuid = db_data.get('uuid')
        if source_db_uuid:
            print(f"📦 Source DB UUID in ZIP: {source_db_uuid}")
        else:
            print(f"⚠️ Cannot find UUID in ZIP: {zip_path}; using generated target UUID")
        # Step 2: Process floor maps (generate datasets, charts, update dashboard)
        floors = dash.get("floors", [])
        new_zip_path = zip_path
        created_datasets = []
        created_charts = []
        # Always clean up old MAP charts and datasets from previous state for this dashboard
        dashboard_key = dash.get("path")
        cleanup_old_map_charts_and_datasets_from_state(dashboard_key)
        if floors:
            print(f"🗺️ Processing {len(floors)} floor maps...")
            # Offset chart IDs per dashboard to avoid collisions
            starting_chart_id = 100 + dash_index * 1000
            new_zip_path, created_datasets, created_charts = process_floor_maps(
                zip_path, floors, target_db_uuid,
                conn_config=conn_config, db_display_name=new_name,
                starting_chart_id=starting_chart_id
            )
            if not new_zip_path:
                continue
        else:
            print("ℹ️ No floors configured, patching database credentials only...")
            extract_dir = find_extract_dir(zip_path)
            if extract_dir:
                update_database_yaml_credentials(extract_dir, conn_config, new_name, target_db_uuid)
                update_dataset_database_uuid(extract_dir, target_db_uuid)
                new_zip_path = rezip_dashboard_export(extract_dir, zip_path)
            else:
                print("❌ Could not extract ZIP for credential patching")
        # Step 3: Update database via Python app-context (reliable non-interactive execution)
        print(f"🔄 Updating Database UUID {target_db_uuid}...")
        python_code = "\n".join([
            "from superset.app import create_app",
            "app = create_app()",
            "with app.app_context():",
            "    from superset import db",
            "    from superset.models.core import Database",
            f"    target_uuid = {json.dumps(target_db_uuid)}",
            f"    target_name = {json.dumps(new_name)}",
            f"    target_uri = {json.dumps(new_uri)}",
            "    database = db.session.query(Database).filter_by(uuid=target_uuid).first()",
            "    if database:",
            "        print(f'Updating existing database: {database.database_name}')",
            "        database.database_name = target_name",
            "        database.sqlalchemy_uri = target_uri",
            "    else:",
            "        print(f'Database with UUID {target_uuid} not found. Creating new one...')",
            "        database = Database(database_name=target_name, sqlalchemy_uri=target_uri, uuid=target_uuid)",
            "        db.session.add(database)",
            "    db.session.commit()",
            "    refreshed = db.session.query(Database).filter_by(uuid=target_uuid).first()",
            "    if refreshed:",
            "        print(f'✅ Database sync complete: name={refreshed.database_name}, uri={refreshed.sqlalchemy_uri}')",
        ])
        res = subprocess.run(["python", "-c", python_code], text=True, capture_output=True)
        if res.returncode != 0:
            print(f"❌ Error updating database via Python app-context: {res.stderr}")
            continue
        else:
            print(res.stdout)
        print(f"🚀 Importing Dashboard: {new_zip_path}")
        subprocess.run(["superset", "import-dashboards", "-p", new_zip_path, "-u", "admin"])
        
        # Save state after successful import (keyed by dashboard path)
        state = {
            'floors': floors,
            'datasets': created_datasets,
            'charts': created_charts,
            'database': {
                'uuid': target_db_uuid,
                'name': new_name,
                'host': conn_config['host'],
                'port': conn_config['port'],
                'db': conn_config['db']
            },
            'zip_path': dash.get("path")
        }
        write_state(state, dashboard_key)
        
        # Cleanup: Delete extracted dashboard_export_* folder and the new zip after import
        base_dir = os.path.dirname(zip_path)
        for extract_dir in glob.glob(os.path.join(base_dir, 'dashboard_export_*')):
            if os.path.isdir(extract_dir):
                shutil.rmtree(extract_dir)
                print(f"🧹 Cleaned up extracted folder: {os.path.basename(extract_dir)}")
        if new_zip_path != zip_path and os.path.isfile(new_zip_path):
            os.remove(new_zip_path)
            print(f"🧹 Deleted imported zip: {os.path.basename(new_zip_path)}")

if __name__ == "__main__":
    update_via_superset_shell()