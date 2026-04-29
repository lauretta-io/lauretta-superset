import json, os, zipfile, yaml, subprocess, uuid, shutil, re, glob, string, random

CONFIG_PATH = "/app/lauretta/dashboards/config.json"

def check_dashboard_exists(zip_path):
    """Check if the dashboard from the given ZIP has already been imported into Superset.
    Reads the dashboard UUID from the zip then queries the Dashboard model.
    Returns True if a Dashboard record with that UUID exists in the metadata DB."""
    dashboard_uuid = None
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            dash_files = [n for n in z.namelist() if '/dashboards/' in n and n.endswith('.yaml')]
            if dash_files:
                with z.open(dash_files[0]) as f:
                    dash_data = yaml.safe_load(f)
                    dashboard_uuid = dash_data.get('uuid')
    except Exception as e:
        print(f"⚠️ Could not read dashboard UUID from zip: {e}")
        return False

    if not dashboard_uuid:
        print("⚠️ No dashboard UUID found in zip — treating as new")
        return False

    python_code = "\n".join([
        "from superset.app import create_app",
        "app = create_app()",
        "with app.app_context():",
        "    from superset import db",
        "    from superset.models.dashboard import Dashboard",
        f"    result = db.session.query(Dashboard).filter_by(uuid={json.dumps(dashboard_uuid)}).first()",
        "    print('EXISTS' if result else 'NOT_FOUND')",
    ])
    res = subprocess.run(["python", "-c", python_code], text=True, capture_output=True)
    if res.returncode != 0:
        print(f"⚠️ Error checking dashboard existence: {res.stderr}")
        return False
    return 'EXISTS' in res.stdout

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

def create_default_dataset_template(db_uuid):
    """Create a single Floor Maps Dataset template (no floor_id filter in SQL)."""
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
{% set floor_filter = filter_values('floor_id') %}
WITH property_footfall AS (
    SELECT
            SUM(pd.footfall_zo) AS prop_footfall
    FROM property.property_summary{{ suffix }} pd
    WHERE 1=1
        {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
        {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
),
main_query AS (
    SELECT
            res.floor_id,
            res.zone_name,
            res.name,
            res.layer,
            res.category,
            res.points,
            res.total_footfall,
            ROUND(
                100 * (
                    res.total_footfall::NUMERIC
                    / NULLIF(property_footfall.prop_footfall::NUMERIC, 0)
                ),
                2
            ) AS percentage_of_prop,
            COALESCE(
                res.event_time,
                CAST({{ "'" + start_date + "'" if from_dttm else start_date }} AS TIMESTAMP)
            ) AS event_time
    FROM (
            -- Units
            SELECT
                    z.floor_id, z.name as zone_name, u.name AS name,
                    'Retail' AS layer,
                    COALESCE(ug.name, 'Uncategorized') AS category,
                    z.points,
                    COALESCE(usd.total_footfall, 0) AS total_footfall,
                        usd.event_time
            FROM property.zones z
            LEFT JOIN property.unit_zone_mappings uzm ON uzm.zone_id = z.id
            LEFT JOIN property.units u ON u.id = uzm.unit_id
            LEFT JOIN property.unit_unit_group_mappings uugm ON uugm.unit_id = u.id
            LEFT JOIN property.unit_groups ug ON ug.id = uugm.unit_group_id
            LEFT JOIN (
                    SELECT unit_id, SUM(footfall_zo) AS total_footfall, MAX({{ t_col }}) as event_time
                    FROM property.unit_summary{{ suffix }}
                    WHERE 1=1
                        {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
                        {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
                    GROUP BY unit_id
            ) usd ON usd.unit_id = u.id
            WHERE u.id IS NOT NULL AND ug.deleted_at IS NULL
            UNION ALL
            -- Public Spaces
            SELECT
                    z.floor_id, z.name as zone_name, ps.name as name,
                    'Public' AS layer,
                    'Public' AS category,
                    z.points,
                    COALESCE(pssd.total_footfall, 0) AS total_footfall,
                        pssd.event_time
            FROM property.public_spaces ps
            LEFT JOIN property.public_space_zone_mappings pszm ON pszm.public_space_id = ps.id
            LEFT JOIN property.zones z ON z.id = pszm.zone_id
            LEFT JOIN (
                    SELECT public_space_id, SUM(footfall_zo) AS total_footfall, MAX({{ t_col }}) as event_time
                    FROM property.public_space_summary{{ suffix }}
                    WHERE 1=1
                        {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
                        {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
                    GROUP BY public_space_id
            ) pssd ON pssd.public_space_id = ps.id
            UNION ALL
            -- Entrances
            SELECT
                    z.floor_id, z.name as zone_name, e.name as name,
                    'Entrances' AS layer,
                    'Entrances' AS category,
                    z.points,
                    COALESCE(esd.total_footfall, 0) AS total_footfall,
                        esd.event_time
            FROM property.entrances e
            LEFT JOIN property.entrance_zone_mappings ezm ON ezm.entrance_id = e.id
            LEFT JOIN property.zones z ON z.id = ezm.zone_id
            LEFT JOIN (
                    SELECT entrance_id, SUM(footfall_zo) AS total_footfall, MAX({{ t_col }}) as event_time
                    FROM property.entrance_summary{{ suffix }}
                    WHERE 1=1
                        {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
                        {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
                    GROUP BY entrance_id
            ) esd ON esd.entrance_id = e.id
            UNION ALL
            -- Escalators
            SELECT
                    z.floor_id, z.name as zone_name, e.name as name,
                    'Circulation' AS layer,
                    'Circulation' AS category,
                    z.points,
                    COALESCE(esd.total_footfall, 0) AS total_footfall,
                        esd.event_time
            FROM property.escalators e
            LEFT JOIN property.escalator_zone_mappings ezm ON ezm.escalator_id = e.id
            LEFT JOIN property.zones z ON z.id = ezm.zone_id
            LEFT JOIN (
                    SELECT escalator_id, SUM(footfall_zo) AS total_footfall, MAX({{ t_col }}) as event_time
                    FROM property.escalator_summary{{ suffix }}
                    WHERE 1=1
                        {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
                        {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
                    GROUP BY escalator_id
            ) esd ON esd.escalator_id = e.id
            UNION ALL
            -- Lift Lobbies
            SELECT
                    z.floor_id, z.name as zone_name, ll.name as name,
                    'Circulation' AS layer,
                    'Circulation' AS category,
                    z.points,
                    COALESCE(llsd.total_footfall, 0) AS total_footfall,
                        llsd.event_time
            FROM property.lift_lobbies ll
            LEFT JOIN property.lift_lobby_zone_mappings llzm ON llzm.lift_lobby_id = ll.id
            LEFT JOIN property.zones z ON z.id = llzm.zone_id
            LEFT JOIN (
                    SELECT lift_lobby_id, SUM(footfall_zo) AS total_footfall, MAX({{ t_col }}) as event_time
                    FROM property.lift_lobby_summary{{ suffix }}
                    WHERE 1=1
                        {% if from_dttm %} AND {{ t_col }}::timestamp >= '{{ from_str.replace("T", " ") }}'::timestamp {% endif %}
                        {% if to_dttm %} AND {{ t_col }}::timestamp < '{{ to_str.replace("T", " ") }}'::timestamp {% endif %}
                    GROUP BY lift_lobby_id
            ) llsd ON llsd.lift_lobby_id = ll.id
            UNION ALL
            -- Dummy row (always present, mirrors all active filters)
            SELECT {{ floor_filter[0] if floor_filter else 0 }}                                                                                          AS floor_id,
                    'No Data'                                                                                                                              AS zone_name,
                    'No Data'                                                                                                                              AS name,
                    'None'                                                                                                                                 AS layer,
                    'None'                                                                                                                                 AS category,
                    NULL                                                                                                                                   AS points,
                    0                                                                                                                                      AS total_footfall,
                    {% if from_str %}
                        TO_TIMESTAMP('{{ from_str.replace("T", " ") }}', 'YYYY-MM-DD HH24:MI:SS')
                    {% else %}
                        CURRENT_TIMESTAMP
                    {% endif %}                                   AS event_time

    ) res
    CROSS JOIN property_footfall
    WHERE res.name IS NOT NULL OR res.name = 'No Data'
)
SELECT * FROM main_query  
"""
    return {
        'table_name': 'Floor Map Summary',
        'main_dttm_col': None,
        'description': None,
        'default_endpoint': None,
        'offset': 0,
        'cache_timeout': None,
        'catalog': 'property',
        'schema': 'property',
        'sql': sql_template,
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
                'column_name': 'percentage_of_prop',
                'verbose_name': None,
                'is_dttm': False,
                'is_active': True,
                'type': 'DECIMAL',
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
                'column_name': 'layer',
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
            },

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

def create_default_chart_template(floor_name, floor_id, chart_id, dataset_uuid, floor_image=''):
    """Create a default MAP FLOOR chart template with floor_id default filter."""
    public_floor_image = build_public_floor_image_url(floor_image)

    adhoc_filters = [
        {
            'expressionType': 'SIMPLE',
            'subject': 'datestamp',
            'operator': 'TEMPORAL_RANGE',
            'comparator': 'Last day',
            'clause': 'WHERE',
            'sqlExpression': None,
            'isExtra': False,
            'isNew': False,
            'datasourceWarning': False,
        },
        {
            'expressionType': 'SIMPLE',
            'subject': 'floor_id',
            'operator': '==',
            'operatorId': 'EQUALS',
            'comparator': str(floor_id),
            'clause': 'WHERE',
            'sqlExpression': None,
            'isExtra': False,
            'isNew': False,
            'datasourceWarning': False,
        }
    ]

    params = {
        'viz_type': 'ext-floor-map',
        'slice_id': chart_id,
        'floor_selection': floor_name,
        'floor_image': public_floor_image,
        'cols': ['name', 'category', 'points', 'total_footfall', 'percentage_of_prop', 'layer'],
        'adhoc_filters': adhoc_filters,
        'row_limit': 5000,
        'extra_form_data': {}
    }
    
    query_context = {
        'datasource': {'type': 'table'},
        'force': False,
        'queries': [
            {
                'filters': [
                    {'col': 'datestamp', 'op': 'TEMPORAL_RANGE', 'val': 'Last day'},
                    {'col': 'floor_id', 'op': '==', 'val': str(floor_id)}
                ],
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
                'groupby': ['name', 'category', 'points', 'total_footfall', 'percentage_of_prop', 'layer']
            }
        ],
        'form_data': {
            'viz_type': 'ext-floor-map',
            'slice_id': chart_id,
            'floor_selection': floor_name,
            'floor_image': public_floor_image,
            'cols': ['name', 'category', 'points', 'total_footfall', 'percentage_of_prop', 'layer'],
            'adhoc_filters': adhoc_filters,
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
        'slice_name': f'Floor Map {floor_name} {floor_id}',
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

def generate_floor_datasets(extract_dir, floors, db_uuid):
    """Generate a single Floor Maps Dataset (shared by all floor charts)."""
    datasets_dir = os.path.join(extract_dir, 'datasets', 'None')
    os.makedirs(datasets_dir, exist_ok=True)
    
    # Remove ALL existing MAP_*.yaml files including old per-floor datasets
    if os.path.exists(datasets_dir):
        for f in os.listdir(datasets_dir):
            if f.startswith('MAP_') and f.endswith('.yaml'):
                old_file = os.path.join(datasets_dir, f)
                os.remove(old_file)
                print(f"🧹 Removed dataset: {f}")
    
    # Generate single dataset
    new_dataset = create_default_dataset_template(db_uuid)
    
    filename = "MAP_Floor_Maps_Dataset.yaml"
    filepath = os.path.join(datasets_dir, filename)
    
    with open(filepath, 'w') as f:
        yaml.dump(new_dataset, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    print(f"📊 Created single dataset: {filename} (UUID: {new_dataset['uuid'][:8]}...)")
    
    return {
        'uuid': new_dataset['uuid'],
        'table_name': new_dataset['table_name'],
        'filename': filename
    }

def generate_floor_charts(extract_dir, floors, dataset_info, starting_chart_id=100):
    """Generate chart YAML files for each floor, all sharing a single dataset."""
    charts_dir = os.path.join(extract_dir, 'charts')
    os.makedirs(charts_dir, exist_ok=True)
    
    # Remove ALL existing MAP_FLOOR_*.yaml files including the template
    if os.path.exists(charts_dir):
        for f in os.listdir(charts_dir):
            if f.startswith('MAP_FLOOR_') and f.endswith('.yaml'):
                old_file = os.path.join(charts_dir, f)
                os.remove(old_file)
                print(f"🧹 Removed chart: {f}")
    
    dataset_uuid = dataset_info['uuid']
    created_charts = []
    chart_id = starting_chart_id
    
    for floor in floors:
        floor_name = floor['name']
        floor_id = floor['id']
        
        # Generate chart — all charts share the single dataset UUID
        new_chart = create_default_chart_template(
            floor_name,
            floor_id,
            chart_id,
            dataset_uuid,
            floor.get('image', ''),
        )
        
        # Write chart file — use floor_id to keep unique even when names duplicate
        filename = f"MAP_FLOOR_{floor_id}_{floor_name}_{chart_id}.yaml"
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
    # Expand: time_range, property, floor, categories, stores — all except line_chart_time_range
    filter_names_to_skip = {'line chart time range'}

    if 'native_filter_configuration' in metadata:
        for filter_config in metadata['native_filter_configuration']:
            filter_name = str(filter_config.get('name', '')).strip().lower()
            filter_type = str(filter_config.get('type', '')).strip()
            # Skip dividers and any explicitly excluded filters
            if filter_type == 'DIVIDER' or filter_name in filter_names_to_skip:
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
    
    return map_tab_id  # Return the map tab ID for reference


def update_database_yaml_credentials(extract_dir, conn_config, db_display_name, target_db_uuid, timezone=None):
    """Rewrite databases/*.yaml inside the extracted ZIP with real credentials
    from config.json so the Superset importer won't reject the masked password.
    If timezone is provided, injects engine_params.connect_args.options=-c timezone=<tz>."""
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
        if timezone:
            extra = db_data.get('extra') or {}
            if isinstance(extra, str):
                import json as _json
                extra = _json.loads(extra) if extra else {}
            engine_params = extra.get('engine_params') or {}
            connect_args = engine_params.get('connect_args') or {}
            connect_args['options'] = f'-c timezone={timezone}'
            engine_params['connect_args'] = connect_args
            extra['engine_params'] = engine_params
            db_data['extra'] = extra
            print(f"🕐 Set extra.engine_params.connect_args.options=-c timezone={timezone} in {fname}")
        yaml_str = yaml.dump(db_data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"\n📄 Database YAML [{fname}] before zip/import:\n{'─'*60}\n{yaml_str}{'─'*60}\n")
        with open(fpath, 'w') as f:
            f.write(yaml_str)
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


def process_floor_maps(zip_path, floors, db_uuid, conn_config=None, db_display_name=None, starting_chart_id=100, timezone=None):
    """Process floor maps: generate datasets, charts, and update dashboard.
    Returns tuple: (new_zip_path, dataset_info, created_charts)"""
    
    # Find extraction directory
    extract_dir = find_extract_dir(zip_path)
    if not extract_dir:
        print("❌ Could not find or create extraction directory")
        return None, None, []
    print(f"📂 Processing dashboard in: {extract_dir}")
    
    # Process floor maps if configured
    if floors:
        print(f"🗺️ Processing {len(floors)} floor maps...")
        # Generate single shared dataset
        dataset_info = generate_floor_datasets(extract_dir, floors, db_uuid)
        # Generate charts (all sharing the single dataset)
        created_charts = generate_floor_charts(extract_dir, floors, dataset_info, starting_chart_id=starting_chart_id)
        # Update dashboard with floor map charts
        map_tab_id = update_dashboard_with_charts(extract_dir, created_charts)
    else:
        print("ℹ️ No floors configured, skipping floor map generation")
        dataset_info = None
        created_charts = []
        map_tab_id = None
    
    # Customer Journey is now manually configured - just import as-is from ZIP
    print("ℹ️ Customer Journey components will be imported from ZIP (manual setup)")
    
    # Inject real database credentials into the ZIP before import
    if conn_config and db_display_name:
        update_database_yaml_credentials(extract_dir, conn_config, db_display_name, db_uuid, timezone=timezone)
    update_dataset_database_uuid(extract_dir, db_uuid)
    # Re-zip the modified dashboard export
    new_zip_path = rezip_dashboard_export(extract_dir, zip_path)
    return new_zip_path, dataset_info, created_charts

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
        timezone = conn_config.get("timezone")
        if timezone:
            print(f"🕐 Timezone from connections.timezone: {timezone}")
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
        # Helper: update database connection via Python app-context
        def run_db_update():
            print(f"🔄 Updating Database UUID {target_db_uuid}...")
            # Build the extra JSON with timezone engine_params if configured
            extra_dict = {
                "metadata_params": {},
                "engine_params": {},
                "metadata_cache_timeout": {},
                "schemas_allowed_for_file_upload": []
            }
            if timezone:
                extra_dict["engine_params"] = {
                    "connect_args": {
                        "options": f"-c timezone={timezone}"
                    }
                }
            extra_json = json.dumps(extra_dict)
            python_code = "\n".join([
                "import json",
                "from superset.app import create_app",
                "app = create_app()",
                "with app.app_context():",
                "    from superset import db",
                "    from superset.models.core import Database",
                f"    target_uuid = {json.dumps(target_db_uuid)}",
                f"    target_name = {json.dumps(new_name)}",
                f"    target_uri = {json.dumps(new_uri)}",
                f"    target_extra = {json.dumps(extra_json)}",
                "    database = db.session.query(Database).filter_by(uuid=target_uuid).first()",
                "    if database:",
                "        print(f'Updating existing database: {database.database_name}')",
                "        database.database_name = target_name",
                "        database.sqlalchemy_uri = target_uri",
                "        # Merge engine_params into existing extra",
                "        try:",
                "            existing_extra = json.loads(database.extra) if database.extra else {}",
                "        except json.JSONDecodeError:",
                "            existing_extra = {}",
                "        new_extra = json.loads(target_extra)",
                "        existing_extra['engine_params'] = new_extra.get('engine_params', {})",
                "        database.extra = json.dumps(existing_extra)",
                "    else:",
                "        print(f'Database with UUID {target_uuid} not found. Creating new one...')",
                "        database = Database(database_name=target_name, sqlalchemy_uri=target_uri, uuid=target_uuid, extra=target_extra)",
                "        db.session.add(database)",
                "    db.session.commit()",
                "    refreshed = db.session.query(Database).filter_by(uuid=target_uuid).first()",
                "    if refreshed:",
                "        print(f'✅ Database sync complete: name={refreshed.database_name}, uri={refreshed.sqlalchemy_uri}')",
                "        print(f'   extra={refreshed.extra}')",
            ])
            res = subprocess.run(["python", "-c", python_code], text=True, capture_output=True)
            if res.returncode != 0:
                print(f"❌ Error updating database via Python app-context: {res.stderr}")
                return False
            print(res.stdout)
            return True

        # Step 2: Check if dashboard already exists in DB — if so, only update connection
        floors = dash.get("floors", [])

        if check_dashboard_exists(zip_path):
            print("✅ Dashboard already exists in database — only updating database connection.")
            run_db_update()
            continue

        # First run (or after docker compose down -v): full setup
        new_zip_path = zip_path
        dataset_info = None
        created_charts = []

        # Always process floor maps and customer journey (even if no floors)
        print(f"🗺️ Processing dashboard components...")
        starting_chart_id = 100 + dash_index * 1000
        new_zip_path, dataset_info, created_charts = process_floor_maps(
            zip_path, floors, target_db_uuid,
            conn_config=conn_config, db_display_name=new_name,
            starting_chart_id=starting_chart_id, timezone=timezone
        )
        if not new_zip_path:
            print("❌ Failed to process dashboard components")
            continue

        # Step 3: Update database connection
        if not run_db_update():
            continue

        print(f"🚀 Importing Dashboard: {new_zip_path}")
        result = subprocess.run(
            ["superset", "import-dashboards", "-p", new_zip_path, "-u", "admin"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"❌ Import failed with exit code {result.returncode}")
            if result.stderr:
                print(result.stderr)
            continue
        else:
            print(f"✅ Dashboard imported successfully")
            if result.stdout:
                print(result.stdout)

        # Cleanup extracted folders and temp zip
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