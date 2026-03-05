import json, os, zipfile, yaml, subprocess, uuid, shutil, re, glob, string, random

CONFIG_PATH = "/app/lauretta/dashboards/config.json"
STATE_PATH = "/app/lauretta/dashboards/state.json"

def read_state():
    """Read the current state from state.json."""
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error reading state.json: {e}")
    return {}

def write_state(state):
    """Write the current state to state.json."""
    try:
        with open(STATE_PATH, 'w') as f:
            json.dump(state, f, indent=2)
        print(f"💾 State saved to {STATE_PATH}")
    except Exception as e:
        print(f"⚠️ Error writing state.json: {e}")

def cleanup_old_map_charts_and_datasets_from_state():
    """Remove old MAP FLOOR charts and MAP datasets from the database using state.json.
    Only deletes charts/datasets that were previously created (stored in state.json)."""
    state = read_state()
    
    old_charts = state.get('charts', [])
    old_datasets = state.get('datasets', [])
    
    if not old_charts and not old_datasets:
        print("ℹ️ No previous state found, nothing to clean up")
        return
    
    chart_names = [c['slice_name'] for c in old_charts]
    dataset_names = [d['table_name'] for d in old_datasets]
    
    chart_names_str = str(chart_names)
    dataset_names_str = str(dataset_names)
    
    python_code = f"""
from superset import db
from superset.models.slice import Slice
from superset.connectors.sqla.models import SqlaTable

chart_names = {chart_names_str}
dataset_names = {dataset_names_str}

# Delete MAP FLOOR charts (slices) from previous state
map_charts = db.session.query(Slice).filter(Slice.slice_name.in_(chart_names)).all()
for chart in map_charts:
    print(f'🧹 Deleting chart: {{chart.slice_name}} (ID: {{chart.id}})')
    db.session.delete(chart)

# Delete MAP datasets from previous state
map_datasets = db.session.query(SqlaTable).filter(SqlaTable.table_name.in_(dataset_names)).all()
for dataset in map_datasets:
    print(f'🧹 Deleting dataset: {{dataset.table_name}} (ID: {{dataset.id}})')
    db.session.delete(dataset)

db.session.commit()
print(f'✅ Cleaned up {{len(map_charts)}} charts and {{len(map_datasets)}} datasets')
"""
    print(f"🧹 Cleaning up old items from state.json: {len(chart_names)} charts, {len(dataset_names)} datasets...")
    res = subprocess.run(["superset", "shell"], input=python_code, text=True, capture_output=True)
    if res.returncode != 0:
        print(f"⚠️ Warning during cleanup: {res.stderr}")
    else:
        print(res.stdout)


def generate_uuid():
    """Generate a random UUID string."""
    return str(uuid.uuid4())

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
    sql_template = """{% set start_date = from_dttm if from_dttm else "CURRENT_DATE - INTERVAL '1 day'" %}
{% set end_date = to_dttm if to_dttm else "CURRENT_DATE" %}

SELECT 
    res.floor_id,
    res.zone_name,
    res.name,
    res.category,
    res.points,
    res.total_footfall,
    COALESCE(res.datestamp, CAST({{ "'" + start_date + "'" if from_dttm else start_date }} AS TIMESTAMP)) AS datestamp
FROM (
    -- Units
    SELECT
        z.floor_id, z.name as zone_name, u.name AS name, ug.name AS category, z.points,
        COALESCE(usd.total_footfall, 0) AS total_footfall,
        usd.datestamp
    FROM property.zones z
    LEFT JOIN property.unit_zone_mappings uzm ON uzm.zone_id = z.id
    LEFT JOIN property.units u ON u.id = uzm.unit_id
    LEFT JOIN property.unit_unit_group_mappings uugm ON uugm.unit_id = u.id
    LEFT JOIN property.unit_groups ug ON ug.id = uugm.unit_group_id
    LEFT JOIN (
        SELECT unit_id, SUM(footfall) AS total_footfall, MAX(datestamp) as datestamp
        FROM property.unit_summary_daily
        WHERE datestamp >= {{ "'" + start_date + "'" if from_dttm else start_date }}
          AND datestamp < {{ "'" + end_date + "'" if to_dttm else end_date }}
        GROUP BY unit_id
    ) usd ON usd.unit_id = u.id
    WHERE z.floor_id = {FLOOR_ID}
      {% if filter_values('unit_name') %} AND u.name IN ({{ "'" + filter_values('unit_name') | join("','") + "'" }}) {% endif %}
      {% if filter_values('unit_group_name') %} AND ug.name IN ({{ "'" + filter_values('unit_group_name') | join("','") + "'" }}) {% endif %}

    UNION ALL

    -- Public Spaces
    SELECT
        z.floor_id, z.name as zone_name, ps.name as name, 'Public' AS category, z.points,
        COALESCE(pssd.total_footfall, 0) AS total_footfall,
        pssd.datestamp
    FROM property.public_spaces ps 
    LEFT JOIN property.public_space_zone_mappings pszm ON pszm.public_space_id = ps.id 
    LEFT JOIN property.zones z ON z.id = pszm.zone_id 
    LEFT JOIN (
        SELECT public_space_id, SUM(footfall) AS total_footfall, MAX(datestamp) as datestamp
        FROM property.public_space_summary_daily
        WHERE datestamp >= {{ "'" + start_date + "'" if from_dttm else start_date }}
          AND datestamp < {{ "'" + end_date + "'" if to_dttm else end_date }}
        GROUP BY public_space_id
    ) pssd ON pssd.public_space_id = ps.id
    WHERE ps.floor_id = {FLOOR_ID}

    UNION ALL

    -- Entrances
    SELECT
        z.floor_id, z.name as zone_name, e.name as name, 'Entrances' AS category, z.points,
        COALESCE(esd.total_footfall, 0) AS total_footfall,
        esd.datestamp
    FROM property.entrances e
    LEFT JOIN property.entrance_zone_mappings ezm ON ezm.entrance_id = e.id
    LEFT JOIN property.zones z ON z.id = ezm.zone_id
    LEFT JOIN (
        SELECT entrance_id, SUM(footfall) AS total_footfall, MAX(datestamp) as datestamp
        FROM property.entrance_summary_daily
        WHERE datestamp >= {{ "'" + start_date + "'" if from_dttm else start_date }}
          AND datestamp < {{ "'" + end_date + "'" if to_dttm else end_date }}
        GROUP BY entrance_id
    ) esd ON esd.entrance_id = e.id
    WHERE e.floor_id = {FLOOR_ID}

    UNION ALL

    -- Escalators
    SELECT
        z.floor_id, z.name as zone_name, e.name as name, 'Circulation' AS category, z.points,
        COALESCE(esd.total_footfall, 0) AS total_footfall,
        esd.datestamp
    FROM property.escalators e
    LEFT JOIN property.escalator_zone_mappings ezm ON ezm.escalator_id = e.id
    LEFT JOIN property.zones z ON z.id = ezm.zone_id
    LEFT JOIN (
        SELECT escalator_id, SUM(footfall) AS total_footfall, MAX(datestamp) as datestamp
        FROM property.escalator_summary_daily
        WHERE datestamp >= {{ "'" + start_date + "'" if from_dttm else start_date }}
          AND datestamp < {{ "'" + end_date + "'" if to_dttm else end_date }}
        GROUP BY escalator_id
    ) esd ON esd.escalator_id = e.id
    WHERE z.floor_id = {FLOOR_ID}

    UNION ALL

    -- Lift Lobbies
    SELECT
        z.floor_id, z.name as zone_name, ll.name as name, 'Circulation' AS category, z.points,
        COALESCE(llsd.total_footfall, 0) AS total_footfall,
        llsd.datestamp
    FROM property.lift_lobbies ll
    LEFT JOIN property.lift_lobby_zone_mappings llzm ON llzm.lift_lobby_id = ll.id
    LEFT JOIN property.zones z ON z.id = llzm.zone_id
    LEFT JOIN (
        SELECT lift_lobby_id, SUM(footfall) AS total_footfall, MAX(datestamp) as datestamp
        FROM property.lift_lobby_summary_daily
        WHERE datestamp >= {{ "'" + start_date + "'" if from_dttm else start_date }}
          AND datestamp < {{ "'" + end_date + "'" if to_dttm else end_date }}
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
                'column_name': 'datestamp',
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
                'column_name': 'total_footfall',
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

def create_default_chart_template(floor_name, chart_id, dataset_uuid, floor_image=''):
    """Create a default MAP FLOOR chart template structure when no template exists in ZIP."""
    params = {
        'viz_type': 'ext-floor-map',
        'slice_id': chart_id,
        'floor_selection': floor_name,
        'floor_image': floor_image,
        'cols': ['name', 'category', 'points', 'total_footfall'],
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
        'extra_form_data': {},
        'dashboards': [1]
    }
    
    query_context = {
        'datasource': {'type': 'table'},
        'force': False,
        'queries': [
            {
                'filters': [{'col': 'datestamp', 'op': 'TEMPORAL_RANGE', 'val': 'Last day'}],
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
                'groupby': ['name', 'category', 'points', 'total_footfall']
            }
        ],
        'form_data': {
            'viz_type': 'ext-floor-map',
            'slice_id': chart_id,
            'floor_selection': floor_name,
            'floor_image': floor_image,
            'cols': ['name', 'category', 'points', 'total_footfall'],
            'adhoc_filters': params['adhoc_filters'],
            'row_limit': 5000,
            'extra_form_data': {},
            'dashboards': [1],
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
    template_path = find_template_dataset(extract_dir)
    use_generated_template = False
    
    if template_path:
        with open(template_path, 'r') as f:
            template = yaml.safe_load(f)
        # Store template UUID so we can remove related chart references
        template_uuid = template.get('uuid')
        # Find the datasets directory (use the same parent as template)
        datasets_dir = os.path.dirname(template_path)
        # Use first floor's ID from config as template_floor_id for replacement
        template_floor_id = floors[0]['id'] if floors else 1
    else:
        print("⚠️ No MAP_*.yaml template found - using generated template")
        use_generated_template = True
        # Find or create datasets directory
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
        
        if use_generated_template:
            # Generate dataset from scratch using template function
            new_dataset = create_default_dataset_template(floor_id, floor_name, db_uuid)
        else:
            # Create new dataset based on existing template
            new_dataset = template.copy()
            new_dataset['table_name'] = f"MAP {floor_name}"
            new_dataset['uuid'] = generate_uuid()
            new_dataset['database_uuid'] = db_uuid
            
            # Replace floor_id in SQL
            if new_dataset.get('sql'):
                new_dataset['sql'] = replace_floor_id_in_sql(
                    new_dataset['sql'], template_floor_id, floor_id
                )
        
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
    template_path = find_template_chart(extract_dir)
    use_generated_template = False
    
    if template_path:
        with open(template_path, 'r') as f:
            template = yaml.safe_load(f)
        # Store template UUID to remove from dashboard later
        template_chart_uuid = template.get('uuid')
    else:
        print("⚠️ No MAP_FLOOR_*.yaml template found - using generated template")
        use_generated_template = True
    
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
        
        if use_generated_template:
            # Generate chart from scratch using template function
            new_chart = create_default_chart_template(floor_name, chart_id, dataset['uuid'], floor.get('image', ''))
        else:
            # Create new chart based on existing template
            new_chart = template.copy()
            new_chart['slice_name'] = f"MAP FLOOR {floor_name}"
            new_chart['uuid'] = generate_uuid()
            new_chart['dataset_uuid'] = dataset['uuid']
            
            # Update params
            if new_chart.get('params'):
                params = new_chart['params']
                if isinstance(params, str):
                    params = yaml.safe_load(params) if params else {}
                params['floor_selection'] = floor_name
                params['floor_image'] = floor.get('image', '')
                params['slice_id'] = chart_id
                new_chart['params'] = params
            
            # Update query_context if present
            if new_chart.get('query_context'):
                try:
                    qc = json.loads(new_chart['query_context'])
                    if 'form_data' in qc:
                        qc['form_data']['floor_selection'] = floor_name
                        qc['form_data']['floor_image'] = floor.get('image', '')
                        qc['form_data']['slice_id'] = chart_id
                    new_chart['query_context'] = json.dumps(qc)
                except:
                    pass
        
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
    
    # Find the Map View tab row (ROW-vocCgVjRwqGFc1NIzzjC4 or similar)
    position = dashboard.get('position', {})
    
    # Remove ALL existing MAP FLOOR charts (including the original template)
    charts_to_remove = []
    for key, value in list(position.items()):
        if isinstance(value, dict) and value.get('type') == 'CHART':
            slice_name = value.get('meta', {}).get('sliceName', '')
            # Remove ALL MAP FLOOR charts
            if slice_name.startswith('MAP FLOOR '):
                charts_to_remove.append(key)
    
    # Collect rows that contained removed charts (to clean up empty rows later)
    rows_with_removed_charts = set()
    
    for chart_key in charts_to_remove:
        # Also remove from parent row's children
        chart_entry = position.get(chart_key, {})
        parents = chart_entry.get('parents', [])
        if parents:
            parent_row = parents[-1]
            if parent_row in position and 'children' in position[parent_row]:
                if chart_key in position[parent_row]['children']:
                    position[parent_row]['children'].remove(chart_key)
                    rows_with_removed_charts.add(parent_row)
        del position[chart_key]
        print(f"🧹 Removed chart entry from dashboard: {chart_key}")
    
    # Find the Map View tab
    map_tab_id = None
    for key, value in position.items():
        if isinstance(value, dict) and value.get('type') == 'TAB':
            meta = value.get('meta', {})
            if meta.get('text') == 'Map View':
                map_tab_id = key
                break
    
    if not map_tab_id:
        print("⚠️ Map View tab not found in dashboard")
        return
    
    # Remove any empty rows that were used for map charts (in Map View tab)
    for row_id in rows_with_removed_charts:
        if row_id in position:
            row_entry = position[row_id]
            # Only remove if row is empty and in Map View tab
            if not row_entry.get('children') and map_tab_id in row_entry.get('parents', []):
                # Remove row from tab's children
                if map_tab_id in position and 'children' in position[map_tab_id]:
                    if row_id in position[map_tab_id]['children']:
                        position[map_tab_id]['children'].remove(row_id)
                del position[row_id]
                print(f"🧹 Removed empty row: {row_id}")
    
    # Determine the tabs hierarchy path to Map View tab
    tabs_container_id = 'TABS-zWZSDwlKZqxTlaw8pvf1O'  # Main tabs container
    base_parents = ['ROOT_ID', 'GRID_ID', tabs_container_id, map_tab_id]
    
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
    
    # Update native_filter_configuration chartsInScope for filters targeting Map View tab
    if 'native_filter_configuration' in metadata:
        for filter_config in metadata['native_filter_configuration']:
            scope = filter_config.get('scope', {})
            root_path = scope.get('rootPath', [])
            # If filter applies to Map View tab
            if map_tab_id in root_path or 'ROOT_ID' in root_path:
                if 'chartsInScope' in filter_config:
                    filter_config['chartsInScope'] = update_charts_in_scope(
                        filter_config['chartsInScope'], new_chart_ids
                    )
    
    dashboard['position'] = position
    dashboard['metadata'] = metadata
    
    # Write updated dashboard
    with open(dashboard_path, 'w') as f:
        yaml.dump(dashboard, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    print(f"✅ Dashboard updated with {len(created_charts)} new floor map charts")

def process_floor_maps(zip_path, floors, db_uuid):
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
    created_charts = generate_floor_charts(extract_dir, floors, created_datasets)
    # Update dashboard
    update_dashboard_with_charts(extract_dir, created_charts)
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
    for dash in config.get("dashboards", []):
        zip_path = dash.get("path")
        if zip_path.startswith('/lauretta/'): zip_path = '/app' + zip_path
        conn_config = dash.get("connections")
        new_name = conn_config.get("database_display_name", conn_config.get("database_name", "Database"))
        new_uri = f"postgresql+psycopg2://{conn_config['username']}:{conn_config['password']}@{conn_config['host']}:{conn_config['port']}/{conn_config['db']}"
        # Step 1: Trace UUID from file ZIP
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
        # Step 2: Process floor maps (generate datasets, charts, update dashboard)
        floors = dash.get("floors", [])
        new_zip_path = zip_path
        created_datasets = []
        created_charts = []
        # Always clean up old MAP charts and datasets from previous state
        cleanup_old_map_charts_and_datasets_from_state()
        if floors:
            print(f"🗺️ Processing {len(floors)} floor maps...")
            new_zip_path, created_datasets, created_charts = process_floor_maps(zip_path, floors, old_db_uuid)
            if not new_zip_path:
                continue
        else:
            print("ℹ️ No floors configured, state.json will be cleared")
        # Step 3: Update database via superset shell
        python_code = f"""
from superset import db
from superset.models.core import Database

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
        print(f"🚀 Importing Dashboard: {new_zip_path}")
        subprocess.run(["superset", "import-dashboards", "-p", new_zip_path, "-u", "admin"])
        
        # Save state after successful import
        state = {
            'floors': floors,
            'datasets': created_datasets,
            'charts': created_charts,
            'database': {
                'uuid': old_db_uuid,
                'name': new_name,
                'host': conn_config['host'],
                'port': conn_config['port'],
                'db': conn_config['db']
            },
            'zip_path': dash.get("path")
        }
        write_state(state)
        
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