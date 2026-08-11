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

# Getting Started with Superset using Docker

Docker is an easy way to get started with Superset.

## Prerequisites

1. [Docker](https://www.docker.com/get-started)
2. [Docker Compose](https://docs.docker.com/compose/install/)

## Configuration

The `/app/pythonpath` folder is mounted from [`./docker/pythonpath_dev`](./pythonpath_dev)
which contains a base configuration [`./docker/pythonpath_dev/superset_config.py`](./pythonpath_dev/superset_config.py)
intended for use with local development.

### Local overrides

In order to override configuration settings locally, simply make a copy of [`./docker/pythonpath_dev/superset_config_local.example`](./pythonpath_dev/superset_config_local.example)
into `./docker/pythonpath_dev/superset_config_docker.py` (git ignored) and fill in your overrides.

### Local packages

If you want to add Python packages in order to test things like databases locally, you can simply add a local requirements.txt (`./docker/requirements-local.txt`)
and rebuild your Docker stack.

Steps:

1. Create `./docker/requirements-local.txt`
2. Add your new packages
3. Rebuild docker compose
    1. `docker compose down -v`
    2. `docker compose up`

## Initializing Database

The database will initialize itself upon startup via the init container ([`superset-init`](./docker-init.sh)). This may take a minute.

## Normal Operation

To run the container, simply run: `docker compose up`

After waiting several minutes for Superset initialization to finish, you can open a browser and view [`http://localhost:9000`](http://localhost:9000)
to start your journey.

Note the port: `docker compose up` is dev mode, where `DEV_MODE=true` skips the
frontend build. The app on `:8088` therefore serves HTML with no assets behind it and
renders unstyled; `:9000` is the webpack dev server, which serves the assets and
proxies everything else to the app. `:8088` is the right port for
`docker-compose-non-dev.yml`, which bakes the bundle into the image. See
[`DEPLOYMENT.md`](../DEPLOYMENT.md).

## Developing

While running, the container server will reload on modification of the Superset Python and JavaScript source code.
Don't forget to reload the page to take the new frontend into account though.

## Production

It is possible to run Superset in non-development mode by using [`docker-compose-non-dev.yml`](../docker-compose-non-dev.yml). This file excludes the volumes needed for development. For a hardened, internet-facing deployment see [`docker-compose-secure.yml`](../docker-compose-secure.yml) and [`DEPLOYMENT.md`](../DEPLOYMENT.md).

## Resource Constraints

If you are attempting to build on macOS and it exits with 137 you need to increase your Docker resources. See instructions [here](https://docs.docker.com/docker-for-mac/#advanced) (search for memory)
