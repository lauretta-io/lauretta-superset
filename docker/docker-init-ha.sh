#!/usr/bin/env bash
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# One-shot init for HA mode (docker-compose-ha.yml). See lauretta/docs/11-ha-mode.md.
#
#   docker compose -f docker-compose-ha.yml run --rm --no-deps superset /app/docker/docker-init-ha.sh
#
# Run it on ONE node only, while no Superset web of any node uses the metadata database:
# first install, and every Superset upgrade (stop all nodes, run this once, start them).
# The web containers never migrate or import anything at start; two nodes doing it at once
# would race (concurrent CREATEs are not serialised by checkfirst).
#
# Steps, all idempotent:
#   1. superset db upgrade
#   2. admin user (only if it does not exist yet; ADMIN_PASSWORD is required)
#   3. superset init (roles and permissions)
#   4. the server-side session table (lauretta_ha.SESSION_TABLE)
#   5. Lauretta dashboard import, only with HA_IMPORT_DASHBOARDS=yes
set -euo pipefail

# lauretta_ha.init_sessions() refuses to start without the session table, which step 4
# creates only after the CLI commands below have run.
export SUPERSET_HA_INIT=1

step() { printf '######## HA init %s: %s\n' "$1" "$2"; }

if [ -z "${ADMIN_PASSWORD:-}" ] || [ "${ADMIN_PASSWORD}" = "admin" ]; then
    echo "ERROR: set a unique ADMIN_PASSWORD in the HA env file (never the default 'admin')" >&2
    exit 1
fi

step 1/5 "superset db upgrade"
superset db upgrade

step 2/5 "admin user ${ADMIN_USERNAME:-admin}"
# create-admin only prints a message and exits 0 when the user exists, so this neither
# fails nor resets an existing password on a re-run.
superset fab create-admin \
    --username "${ADMIN_USERNAME:-admin}" \
    --email "${ADMIN_EMAIL:-admin@superset.com}" \
    --password "$ADMIN_PASSWORD" \
    --firstname Superset \
    --lastname Admin

step 3/5 "superset init"
superset init

step 4/5 "session table"
python - <<'EOF'
from superset.app import create_app

import lauretta_ha

app = create_app()
lauretta_ha.create_session_table(app)
print(f"session table {lauretta_ha.SESSION_TABLE} ready")
EOF

if [ "${HA_IMPORT_DASHBOARDS:-no}" = "yes" ]; then
    step 5/5 "Lauretta dashboard import"
    python /app/docker/import-dashboards.py
else
    step 5/5 "dashboard import skipped (HA_IMPORT_DASHBOARDS is not yes)"
fi
echo "HA init complete. Start the web on every node."
