<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# AD login: off

docker-compose-ha.yml mounts this directory at `/app/ldap` when `SUPERSET_LDAP_DIR` is
unset. It has no `ldap.json`, so Superset keeps its own password login. Do not add
files here: point `SUPERSET_LDAP_DIR` at a directory outside the checkout instead.
See lauretta/docs/13-ad-login.md.
