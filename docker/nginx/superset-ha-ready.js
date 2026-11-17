/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

/*
 * GET /ready for HA mode (njs, loaded by docker/nginx/superset-ha.conf).
 *
 * Reads the status file a host-side producer writes (atomically: temp file + rename)
 * into the directory mounted at /run/superset-ha:
 *
 *   {"ready": true, "at": <unix seconds>, ...anything else, ignored here}
 *
 * 200 "ready" only if `ready` is true and `at` is less than MAX_AGE_S old; otherwise
 * 503. A missing, unreadable or stale file is "not ready", so a dead producer can never
 * leave a node advertised. The body never says why: the reasons stay in the file on
 * the host, for operators.
 */
import fs from 'fs';

const STATUS_FILE = '/run/superset-ha/ready.json';
const MAX_AGE_S = 30;

function reply(r, code, text) {
    r.headersOut['Content-Type'] = 'text/plain';
    r.headersOut['Cache-Control'] = 'no-store';
    r.return(code, text + '\n');
}

function serve(r) {
    let status;
    try {
        status = JSON.parse(fs.readFileSync(STATUS_FILE, 'utf8'));
    } catch (e) {
        return reply(r, 503, 'not ready');
    }
    const age = Date.now() / 1000 - Number(status.at);
    // A little slack for clock steps; the producer and nginx share the host clock.
    if (!(age > -5 && age < MAX_AGE_S)) {
        return reply(r, 503, 'not ready');
    }
    return status.ready === true ? reply(r, 200, 'ready') : reply(r, 503, 'not ready');
}

export default { serve };
