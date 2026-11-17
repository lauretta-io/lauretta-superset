#!/usr/bin/env bash

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

set -euo pipefail

host="${DATABASE_HOST:-db}"
deadline=$((SECONDS + 60))
until pg_isready -h "${host}" -U "${POSTGRES_USER}"; do
    if (( SECONDS >= deadline )); then
        echo "Postgres at ${host} did not become ready within 60 seconds" >&2
        exit 1
    fi
    sleep 1
done

PGPASSWORD="${POSTGRES_PASSWORD}" psql -X -v ON_ERROR_STOP=1 \
    -h "${host}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    -v examples_user="${EXAMPLES_USER}" \
    -v examples_password="${EXAMPLES_PASSWORD}" \
    -v examples_db="${EXAMPLES_DB}" <<'EOSQL'
SELECT format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'examples_user', :'examples_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'examples_user')
\gexec
SELECT format('CREATE DATABASE %I', :'examples_db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'examples_db')
\gexec
SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'examples_db', :'examples_user')
\gexec
SELECT format('CREATE DATABASE %I', 'superset_cypress')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'superset_cypress')
\gexec
EOSQL
