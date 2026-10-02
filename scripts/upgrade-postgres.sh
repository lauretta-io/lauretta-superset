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
# Moves a stack's metadata database onto the Postgres major version its compose
# file pins, by dump and restore. See DEPLOYMENT.md ("Upgrading the Postgres major
# version") for the reasoning behind each step.
#
#   ./scripts/upgrade-postgres.sh <dev|nondev|secure> [options]
#
# Options:
#   --dump-dir DIR   where the volume tarball and dumps go
#                    (default: $HOME/superset-pg-upgrade)
#   --rollback       put the pre-upgrade cluster back instead of upgrading
#   -y, --yes        do not ask for confirmation
#
# Works offline. Only two images are used, and both must already be local:
# postgres:<old> to read the existing cluster and postgres:<new> for everything
# else. Nothing is pulled. To stage them on an air-gapped host:
#
#   docker save postgres:15 postgres:17 | gzip > pg.tar.gz     # connected host
#   gunzip -c pg.tar.gz | docker load                            # air-gapped host
#
# Stops the stack and leaves only `db` running afterwards; starting the rest (which
# runs `superset db upgrade`) is left to you, so the two migrations stay separate.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    sed -n '/^# Moves a stack/,/^# runs `superset db upgrade`/s/^# \{0,1\}//p' "$0" >&2
    exit "${1:-2}"
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

log() {
    echo
    echo "==> $*"
}

MODE=""
DUMP_DIR="${HOME}/superset-pg-upgrade"
ROLLBACK=false
ASSUME_YES=false

while [ $# -gt 0 ]; do
    case "$1" in
        dev | nondev | secure) MODE="$1" ;;
        --dump-dir)
            [ $# -ge 2 ] || die "--dump-dir needs a value"
            DUMP_DIR="$2"
            shift
            ;;
        --rollback) ROLLBACK=true ;;
        -y | --yes) ASSUME_YES=true ;;
        -h | --help) usage 0 ;;
        *) usage ;;
    esac
    shift
done
[ -n "${MODE}" ] || usage

case "${MODE}" in
    dev) COMPOSE_FILE="docker-compose.yml" ;;
    nondev) COMPOSE_FILE="docker-compose-non-dev.yml" ;;
    secure) COMPOSE_FILE="docker-compose-secure.yml" ;;
esac

compose() {
    docker compose --project-directory "${REPO_ROOT}" -f "${REPO_ROOT}/${COMPOSE_FILE}" "$@"
}

command -v docker > /dev/null || die "docker not found"
docker compose version > /dev/null 2>&1 || die "the docker compose plugin is required"

# Resolved from the compose file rather than hardcoded: the project name prefixes
# the volume, and the dev file sets no container_name for db.
COMPOSE_CONFIG="$(compose config)" || die "${COMPOSE_FILE} does not parse; is its env file present?"
PROJECT="$(sed -n 's/^name: //p' <<< "${COMPOSE_CONFIG}")"
VOLUME="${PROJECT}_db_home"
NEW_IMAGE="$(compose config --images db)"
NEW_PG="${NEW_IMAGE##*:}"
NEW_PG="${NEW_PG%%[.-]*}"
PG_USER="$(sed -n 's/^ *POSTGRES_USER: *//p' <<< "${COMPOSE_CONFIG}" | head -n1 | tr -d '"')"
PG_USER="${PG_USER:-postgres}"
SRC_CONTAINER="${PROJECT}-pg-upgrade-src"

[[ "${NEW_IMAGE}" == postgres:* && "${NEW_PG}" =~ ^[0-9]+$ ]] \
    || die "cannot tell the Postgres major version from db's image '${NEW_IMAGE}'"

require_image() {
    docker image inspect "$1" > /dev/null 2>&1 && return
    cat >&2 << EOF
ERROR: image $1 is not available locally, and this script never pulls.
Load it first, e.g. from a host with registry access:
    docker save $1 | gzip > image.tar.gz      # then copy it over
    gunzip -c image.tar.gz | docker load
EOF
    exit 1
}

# Every helper container uses the new Postgres image (Debian-based, so it has sh,
# tar and cp), so the upgrade needs no image beyond the two Postgres versions.
helper() {
    docker run --rm --pull never --entrypoint sh "$@"
}

volume_exists() {
    docker volume inspect "$1" > /dev/null 2>&1
}

pg_version_of() {
    helper -v "$1":/d:ro "${NEW_IMAGE}" -c 'cat /d/PG_VERSION 2>/dev/null || true'
}

copy_volume() {
    helper -v "$1":/from:ro -v "$2":/to "${NEW_IMAGE}" -c 'cp -a /from/. /to/'
}

confirm() {
    ${ASSUME_YES} && return
    printf '\n%s [y/N] ' "$1"
    read -r reply
    [[ "${reply}" =~ ^[Yy] ]] || die "aborted"
}

# Containers left over from an earlier `up`, even stopped ones, pin the volume and
# make `docker volume rm` fail. `down` keeps volumes, so the data is untouched.
stop_stack() {
    log "Stopping the ${MODE} stack (volumes are kept)"
    compose down --remove-orphans
    local users
    users="$(docker ps -a --filter "volume=${VOLUME}" --format '{{.Names}}')"
    [ -z "${users}" ] || die "still attached to ${VOLUME}, remove first: ${users}"
}

# Polls over TCP rather than the socket: on a fresh cluster the entrypoint runs a
# socket-only temporary server for initdb, which would otherwise look ready.
wait_ready() {
    local container="$1" i
    for i in $(seq 1 90); do
        if docker exec "${container}" pg_isready -q -h 127.0.0.1 -U "${PG_USER}" 2> /dev/null; then
            return
        fi
        sleep 2
    done
    docker logs --tail 30 "${container}" >&2 || true
    die "Postgres in ${container} did not become ready"
}

# pg_hba.conf in the official image trusts 127.0.0.1, so joining a container's
# network stack and connecting there needs no password.
psql_in() {
    local container="$1" db="$2"
    shift 2
    docker exec "${container}" psql -X -v ON_ERROR_STOP=1 -U "${PG_USER}" -d "${db}" -At "$@"
}

list_databases() {
    psql_in "$1" postgres -c \
        "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1"
}

count_tables() {
    psql_in "$1" "$2" -c \
        "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
}

rollback() {
    local old_volumes old_volume old_pg
    old_volumes="$(docker volume ls -q | grep -E "^${VOLUME}_pg[0-9]+_old$" || true)"
    [ -n "${old_volumes}" ] || die "no ${VOLUME}_pg<N>_old volume to roll back to"
    [ "$(wc -l <<< "${old_volumes}")" -eq 1 ] || die "several candidates, remove all but one: ${old_volumes}"
    old_volume="${old_volumes}"
    old_pg="$(pg_version_of "${old_volume}")"

    cat << EOF

Rollback, ${MODE} stack (project ${PROJECT})
  ${VOLUME} is replaced by a copy of ${old_volume} (Postgres ${old_pg}).
  Anything written to the database since the upgrade is lost.
EOF
    confirm "Proceed?"
    stop_stack

    log "Restoring ${VOLUME} from ${old_volume}"
    if volume_exists "${VOLUME}"; then
        docker volume rm "${VOLUME}" > /dev/null
    fi
    docker volume create "${VOLUME}" > /dev/null
    copy_volume "${old_volume}" "${VOLUME}"

    cat << EOF

Done. ${VOLUME} is a Postgres ${old_pg} cluster again; ${old_volume} is kept.
Before starting, pin the old version in ${COMPOSE_FILE}, or Postgres refuses the
data directory:

    image: postgres:${old_pg}

then: docker compose -f ${COMPOSE_FILE} up -d
EOF
}

upgrade() {
    volume_exists "${VOLUME}" || die "volume ${VOLUME} does not exist; a fresh stack needs no upgrade"
    require_image "${NEW_IMAGE}"

    local old_pg old_image old_volume stamp
    old_pg="$(pg_version_of "${VOLUME}")"
    [[ "${old_pg}" =~ ^[0-9]+$ ]] || die "${VOLUME} has no readable PG_VERSION; is it a Postgres data directory?"
    if [ "${old_pg}" = "${NEW_PG}" ]; then
        echo "${VOLUME} is already Postgres ${NEW_PG}. Nothing to do."
        return
    fi
    [ "${old_pg}" -lt "${NEW_PG}" ] || die "${VOLUME} is Postgres ${old_pg}, newer than the pinned ${NEW_PG}"

    old_image="postgres:${old_pg}"
    old_volume="${VOLUME}_pg${old_pg}_old"
    stamp="$(date +%Y%m%d-%H%M%S)"
    require_image "${old_image}"
    ! volume_exists "${old_volume}" \
        || die "${old_volume} already exists from an earlier run. Remove it, or roll back with --rollback"

    cat << EOF

Postgres upgrade, ${MODE} stack (project ${PROJECT})
  volume      ${VOLUME}: Postgres ${old_pg} -> ${NEW_PG}
  images      ${old_image}, ${NEW_IMAGE} (local only)
  dump dir    ${DUMP_DIR}
  old cluster kept as volume ${old_volume}

The whole stack is stopped. Only db is running when this finishes.
EOF
    confirm "Proceed?"

    # Both files hold the metadata database, encrypted secrets included, so keep
    # them out of the repo and away from other users.
    mkdir -p "${DUMP_DIR}"
    chmod 700 "${DUMP_DIR}"
    DUMP_DIR="$(cd "${DUMP_DIR}" && pwd)"
    local owner
    owner="$(id -u):$(id -g)"

    stop_stack

    log "Backing up the raw volume"
    local tarball="db_home-pg${old_pg}-${stamp}.tar.gz"
    helper -v "${VOLUME}":/from:ro -v "${DUMP_DIR}":/to "${NEW_IMAGE}" \
        -c "tar czf /to/${tarball} -C /from . && chown ${owner} /to/${tarball}"
    echo "    ${DUMP_DIR}/${tarball}"

    log "Starting the old cluster on its own (${old_image})"
    docker rm -f "${SRC_CONTAINER}" > /dev/null 2>&1 || true
    trap 'docker rm -f "${SRC_CONTAINER}" > /dev/null 2>&1 || true' EXIT
    docker run -d --pull never --name "${SRC_CONTAINER}" \
        -v "${VOLUME}":/var/lib/postgresql/data "${old_image}" > /dev/null
    wait_ready "${SRC_CONTAINER}"

    # Every database is carried over, not just the metadata one: the examples
    # database lives in the same cluster.
    local databases db
    databases="$(list_databases "${SRC_CONTAINER}")"
    [ -n "${databases}" ] || die "the old cluster holds no databases"
    declare -A tables_before
    for db in ${databases}; do
        tables_before[${db}]="$(count_tables "${SRC_CONTAINER}" "${db}")"
    done

    # pg_dump reads servers older than itself but not newer, so the dump uses the
    # new image's client, in a second container sharing the old one's network.
    log "Dumping with the Postgres ${NEW_PG} client"
    for db in ${databases}; do
        docker run --rm --pull never --user "${owner}" \
            --network "container:${SRC_CONTAINER}" -v "${DUMP_DIR}":/out "${NEW_IMAGE}" \
            pg_dump -h 127.0.0.1 -U "${PG_USER}" -d "${db}" -Fc \
            -f "/out/${db}-pg${old_pg}-${stamp}.dump"
        echo "    ${db}: ${tables_before[${db}]} tables -> ${DUMP_DIR}/${db}-pg${old_pg}-${stamp}.dump"
    done

    docker rm -f "${SRC_CONTAINER}" > /dev/null
    trap - EXIT

    # A copy under a second name makes rollback one copy instead of a tar restore.
    log "Keeping the old cluster as ${old_volume}"
    docker volume create "${old_volume}" > /dev/null
    copy_volume "${VOLUME}" "${old_volume}"
    [ "$(pg_version_of "${old_volume}")" = "${old_pg}" ] || die "copy to ${old_volume} looks incomplete; ${VOLUME} left untouched"
    docker volume rm "${VOLUME}" > /dev/null

    log "Creating a fresh Postgres ${NEW_PG} cluster"
    compose up -d --pull never db
    local db_container
    db_container="$(compose ps -q db)"
    [ -n "${db_container}" ] || die "db did not start; see: docker compose -f ${COMPOSE_FILE} logs db"
    wait_ready "${db_container}"

    # initdb has already created POSTGRES_DB, and the init scripts the examples
    # database and its role. Owners are kept, so each role a dump names must exist.
    log "Restoring"
    local failed=()
    for db in ${databases}; do
        if [ -z "$(psql_in "${db_container}" postgres -c "SELECT 1 FROM pg_database WHERE datname = '${db}'")" ]; then
            docker exec "${db_container}" createdb -U "${PG_USER}" "${db}"
        fi
        if ! docker run --rm --pull never --user "${owner}" \
            --network "container:${db_container}" -v "${DUMP_DIR}":/in:ro "${NEW_IMAGE}" \
            pg_restore -h 127.0.0.1 -U "${PG_USER}" -d "${db}" \
            "/in/${db}-pg${old_pg}-${stamp}.dump"; then
            failed+=("${db} (pg_restore reported errors, see above)")
            continue
        fi
        local after
        after="$(count_tables "${db_container}" "${db}")"
        echo "    ${db}: ${after} tables (was ${tables_before[${db}]})"
        [ "${after}" = "${tables_before[${db}]}" ] || failed+=("${db} (table count ${after}, expected ${tables_before[${db}]})")
    done

    if [ ${#failed[@]} -gt 0 ]; then
        echo >&2
        echo "Restore incomplete:" >&2
        printf '    %s\n' "${failed[@]}" >&2
        cat >&2 << EOF

Do not start the rest of the stack. The old cluster is untouched in ${old_volume};
to go back to it:

    ./scripts/upgrade-postgres.sh ${MODE} --rollback
EOF
        exit 1
    fi

    cat << EOF

Done. ${VOLUME} is Postgres $(pg_version_of "${VOLUME}"); only db is running.

Next, start the rest, which applies Superset's own migrations:
    docker compose -f ${COMPOSE_FILE} up -d
    docker compose -f ${COMPOSE_FILE} logs -f superset-init

Once the stack checks out (charts render, saved connections connect), drop the
old cluster and the backups:
    docker volume rm ${old_volume}
    rm ${DUMP_DIR}/*-${stamp}.*

Until then, rolling back is:
    ./scripts/upgrade-postgres.sh ${MODE} --rollback
EOF
}

if ${ROLLBACK}; then
    rollback
else
    upgrade
fi
