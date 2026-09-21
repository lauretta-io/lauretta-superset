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
# Generates docker/.env-secure from docker/.env-secure.example, filling in every
# secret with fresh random values.
#
#   ./scripts/generate-secure-secrets.sh                        # interactive domain prompt
#   ./scripts/generate-secure-secrets.sh superset.example.com   # non-interactive
#
# Refuses to overwrite an existing docker/.env-secure: regenerating SUPERSET_SECRET_KEY
# on a live stack makes stored database passwords undecryptable.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_ROOT}/docker/.env-secure.example"
TARGET="${REPO_ROOT}/docker/.env-secure"

if [ ! -f "${TEMPLATE}" ]; then
    echo "ERROR: template not found at ${TEMPLATE}" >&2
    exit 1
fi

if [ -e "${TARGET}" ]; then
    cat >&2 <<EOF
ERROR: ${TARGET} already exists.

Refusing to overwrite it. Rotating SUPERSET_SECRET_KEY on a stack that already has
a database will make its stored database-connection passwords undecryptable.

To rotate deliberately, set the new key in ${TARGET} by hand, then re-encrypt with
the old one passed on the command line — see DEPLOYMENT.md

To start over from scratch, remove the file and its volumes first.
EOF
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl is required but not installed." >&2
    exit 1
fi

# Base64 with the URL-unsafe characters stripped. Keeps values safe to paste into
# env files, connection URLs and shell arguments without quoting surprises.
rand() {
    openssl rand -base64 "$1" | tr -d '/+=\n' | cut -c "1-$2"
}

DOMAIN="${1:-}"
if [ -z "${DOMAIN}" ]; then
    read -r -p "Public hostname served by nginx (e.g. superset.example.com): " DOMAIN
fi
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%%/*}"
if [ -z "${DOMAIN}" ]; then
    echo "ERROR: a public hostname is required." >&2
    exit 1
fi

SECRET_KEY="$(rand 64 60)"
GUEST_TOKEN_SECRET="$(rand 64 60)"
DB_PASSWORD="$(rand 48 40)"
REDIS_PASSWORD="$(rand 48 40)"
ADMIN_PASSWORD="$(rand 32 24)"
EXAMPLES_PASSWORD="$(rand 48 40)"

# Create with restrictive permissions before writing anything into it.
umask 077
: > "${TARGET}"

# Substitute the <placeholder> values; every other line passes through unchanged so
# the template stays the single source of truth for comments and non-secret settings.
SECRET_KEY="${SECRET_KEY}" \
GUEST_TOKEN_SECRET="${GUEST_TOKEN_SECRET}" \
DB_PASSWORD="${DB_PASSWORD}" \
REDIS_PASSWORD="${REDIS_PASSWORD}" \
ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
EXAMPLES_PASSWORD="${EXAMPLES_PASSWORD}" \
DOMAIN="${DOMAIN}" \
python3 - "${TEMPLATE}" >> "${TARGET}" <<'PY'
import os
import re
import sys

replacements = {
    "SUPERSET_SECRET_KEY": os.environ["SECRET_KEY"],
    "GUEST_TOKEN_SECRET": os.environ["GUEST_TOKEN_SECRET"],
    "DATABASE_PASSWORD": os.environ["DB_PASSWORD"],
    "POSTGRES_PASSWORD": os.environ["DB_PASSWORD"],
    "REDIS_PASSWORD": os.environ["REDIS_PASSWORD"],
    "ADMIN_PASSWORD": os.environ["ADMIN_PASSWORD"],
    "EXAMPLES_PASSWORD": os.environ["EXAMPLES_PASSWORD"],
    "SUPERSET_PUBLIC_URL": f"https://{os.environ['DOMAIN']}",
}

unresolved = []
with open(sys.argv[1]) as handle:
    for line in handle:
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.rstrip("\n"))
        if match:
            key, value = match.group(1), match.group(2)
            if key in replacements:
                print(f"{key}={replacements[key]}")
                continue
            if value.startswith("<") and value.endswith(">"):
                unresolved.append(key)
        sys.stdout.write(line)

if unresolved:
    print(
        "\nNOTE: still needs a value by hand: " + ", ".join(unresolved),
        file=sys.stderr,
    )
PY

chmod 600 "${TARGET}"

cat >&2 <<EOF

Wrote ${TARGET} (mode 600).

  Public URL:     https://${DOMAIN}
  Admin login:    admin / ${ADMIN_PASSWORD}

Next steps:
  1. Uncomment and fill in the SMTP block in ${TARGET} if you want email
     alerts or reports. Leave it commented out and no mail is sent.
  2. sudo chown -R 1000:1000 lauretta/images
  3. docker compose -f docker-compose-secure.yml up -d --build
  4. Change the admin password from the UI after first login.

Back this file up somewhere safe — SUPERSET_SECRET_KEY cannot be regenerated
without re-encrypting every stored database connection.
EOF
