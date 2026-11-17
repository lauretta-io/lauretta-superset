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
# HA-mode web entrypoint (docker-compose-ha.yml, PID 1 of the superset container):
# start gunicorn exactly as every other mode does, but only while a writable metadata
# database answers, and survive the times it does not.
#
# Superset cannot load without one: app creation reads and writes the metadata
# database. Every gunicorn worker loads the app when it boots, and a worker that fails
# to boot makes the gunicorn master exit with code 3 ("Worker failed to boot"). In HA
# mode there are routine windows without a writable server -- a cold start before
# either database is promoted, the minutes of a failover -- and workers are respawned
# at any time (WORKER_MAX_REQUESTS, timeouts, OOM). Measured: one worker killed during
# such a window took the whole container down. So this script:
#   1. waits until a writable metadata database answers (and refuses to go on if the
#      session table is missing: the one-shot init has not run -- that stays loud);
#   2. runs gunicorn (docker-bootstrap.sh app-gunicorn) in its own process group;
#   3. if gunicorn exits with 3 while no writable database answers, goes back to 1;
#      any other exit, or 3 with the database up (a real boot failure), is final.
# While waiting the container keeps running, unhealthy, and /ready answers 503.
# SIGTERM/SIGINT are forwarded to gunicorn (graceful stop) or end the wait at once.
set -uo pipefail

GUNICORN_BOOT_FAILED=3
child=""
stopping=0

on_term() {
    stopping=1
    if [ -n "$child" ]; then
        kill -TERM -- "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null || true
    fi
}
trap on_term TERM INT

# Writable-database check: lauretta_ha.wait_for_metadata(). Exit 0 writable (and the
# session table exists), 1 not writable (--once only), 2 session table missing.
WAIT_FOR_DB=(python -c 'import sys, lauretta_ha; sys.exit(lauretta_ha.wait_for_metadata("--once" in sys.argv))')

# run_bg CMD...: run CMD in its own process group, wait for it; sets $rc. The wait is
# interruptible, so a SIGTERM is handled at once, and after it every process of the
# group is waited for, so gunicorn's graceful shutdown completes before PID 1 exits.
run_bg() {
    setsid "$@" &
    child=$!
    wait "$child"
    rc=$?
    while kill -0 -- "-$child" 2>/dev/null; do
        [ "$stopping" = 1 ] || break
        sleep 0.5
    done
    wait "$child" 2>/dev/null
    child=""
}

while :; do
    run_bg "${WAIT_FOR_DB[@]}"
    [ "$stopping" = 1 ] && exit 0
    [ "$rc" = 0 ] || exit "$rc"

    run_bg /app/docker/docker-bootstrap.sh app-gunicorn
    [ "$stopping" = 1 ] && exit 0
    if [ "$rc" = "$GUNICORN_BOOT_FAILED" ]; then
        run_bg "${WAIT_FOR_DB[@]}" --once
        if [ "$rc" = 1 ]; then
            echo "HA web: gunicorn stopped (worker failed to boot) while no writable" \
                 "metadata database answers; waiting for it again"
            continue
        fi
    fi
    echo "HA web: gunicorn exited with $rc"
    exit "$rc"
done
