# 10 Development

**For:** developers changing the fork's code.

Day-to-day development uses mode 1 (`docker compose up`, browse `:9000`); see
[02 Deployment modes](02-deployment-modes.md). Upstream's contributor guide covers
everything else: <https://superset.apache.org/docs/contributing/development>.

## Custom chart plugins

Two chart types live in `superset-frontend/custom_plugin/`, outside upstream's
`plugins/` tree, so they never conflict on upgrade:

| Package | Chart name in the UI | Viz key | What it shows |
|---|---|---|---|
| `superset-plugin-chart-floor-map` | Floor Map | `ext-floor-map` | Zones drawn as polygons over a floor plan image, or a heatmap; floor-image picker and upload. See [04](04-dashboards-and-floor-maps.md). |
| `superset-plugin-chart-customer-journey` | Customer Journey | `ext-customer-journey` | The most frequent journeys — ordered sequences of nodes (`journey_nodes`) with their counts (`journey_count`). |

Wiring into the frontend build — each of these is an edit to an upstream file:

- `superset-frontend/package.json` — both packages as `file:./custom_plugin/…`
  dependencies, and `custom_plugin/*` in `workspaces`.
- `superset-frontend/lerna.json` — `custom_plugin/*` in `packages`.
- `superset-frontend/tsconfig.json` — a `paths` entry mapping
  `superset-plugin-chart-*` to the plugin sources, and `./custom_plugin/**/*` in
  `include`.
- `superset-frontend/webpack.config.js` — resolve `custom_plugin/` packages from
  `src/`, like the upstream `@superset-ui` packages, and emit `.jpeg` images as
  asset files.
- `superset-frontend/src/visualizations/presets/MainPreset.ts` — registers both
  plugins under their viz keys.

The viz keys are stored in every saved chart. Renaming one orphans those charts.

Each plugin has its own Jest setup (`jest.config.js`, `test/`). The plugin
`README.md` files are the generator template's boilerplate.

## Patches to upstream code

- **Bar chart data-zoom window** (`plugins/plugin-chart-echarts/src/Timeseries/…`:
  `Regular/Bar/controlPanel.tsx`, `constants.ts`, `transformProps.ts`, `types.ts`).
  Adds *Data Zoom Start (%)* and *Data Zoom End (%)* controls, shown when
  *Data Zoom* is enabled, to set the initial visible window.
- `src/explore/components/controls/ZoomConfigControl/ZoomConfigControl.tsx` — removes
  a stray `console.log`.

These are the expensive part of every upgrade; see "Keeping the next one cheap" in
[08](08-upgrading-upstream.md).

## Backend and container changes

- `docker/pythonpath_dev/superset_config.py` and the secure layer — see
  [03 Configuration](03-configuration.md). Python added here is typed and needs to
  pass `pre-commit run mypy`.
- `docker/import-dashboards.py` — see [04](04-dashboards-and-floor-maps.md). To
  change import behaviour, edit it (or `docker/docker-init.sh`, which calls it) and
  rerun `superset-init`; both are bind-mounted, so no rebuild is needed.
- `docker/pythonpath_dev/lauretta_gunicorn.py` — see "Gunicorn worker" in
  [07](07-operations.md).

## Image tags

Every image is tagged `lauretta-superset-<mode>:latest`, pinned explicitly in each
compose file. This matters more than it looks: all three build from the same
`Dockerfile` and the same directory, and an unpinned `image:` makes Compose derive
the tag from the project name plus the service name. Modes 1 and 2 use the same
project name and the same service names, so they collided on one tag — built with
*opposite* `DEV_MODE` values.

| Mode | Tag | Build target | `DEV_MODE` | Frontend |
|---|---|---|---|---|
| dev | `lauretta-superset-dev` | `dev` | `true` | webpack dev server on `:9000` (browse there, not `:8088`); also reachable through the compose nginx on `:80` |
| nondev | `lauretta-superset-nondev` | `dev` | `false` | built into the image |
| secure | `lauretta-superset-secure` | `production` | `false` | built into the image, `SCARF_ANALYTICS=false` |

Two dev-only helper services build different stages and so keep their own tags:
`lauretta-superset-node` (the `superset-node` stage, webpack dev server) and
`lauretta-superset-websocket` (built from `./superset-websocket`).

The dev tag follows `SUPERSET_BUILD_TARGET`, so `SUPERSET_BUILD_TARGET=lean docker
compose up` produces `lauretta-superset-lean` rather than mislabelling a lean build
as `dev`.

All services within a mode share one tag, so each mode builds a single image instead
of four identical ones under per-service names.

## Which service builds

Within each mode, only `superset-init` carries a `build:` block. The other services
declare the same `image:` and add `pull_policy: never`.

The reason is that Compose builds through `buildx bake`, which turns every service
with a `build:` into its own bake target. Because all the Superset services share one
tag, that used to mean four targets writing the same image name in parallel:

```
targets: superset, superset-init, superset-worker, superset-worker-beat
   all four ->  "tags": ["lauretta-superset-nondev:latest"]
```

The legacy overlay2 image store tolerates the duplicate writes — they simply overwrite
each other. The containerd image store, which is the default for fresh Docker 29
installations, rejects the second one:

```
target superset: failed to solve: image
"docker.io/library/lauretta-superset-nondev:latest": already exists
```

This surfaces on a fresh clone on a new server, whether or not `--build` is passed,
and it affected all three modes. `superset-init` is the service that keeps the build
block because it is the only one that can build alone — every other service
`depends_on` it, so naming any of them drags `superset-init` into the build graph and
the collision returns:

```bash
docker compose -f docker-compose-non-dev.yml build --print superset-init
#   targets: ['superset-init']                      <- single target
docker compose -f docker-compose-non-dev.yml build --print superset
#   targets: ['superset', 'superset-init']          <- collision returns
```

`pull_policy: never` stops Compose from trying to pull a tag that exists in no
registry. Without it the stack still starts — Compose falls through to the image
`superset-init` just built — but a cold build first prints one
`pull access denied ... repository does not exist` error per non-building service.

Two consequences worth knowing:

- **Do not add a `build:` block back to the other services.** It looks like an
  oversight and reintroduces the failure, but only on containerd-store hosts, so it
  will pass review on a machine that was upgraded from an older Docker.
- **`docker compose up superset-worker` on its own** now fails with an image-not-found
  error on a host that has never built the image, instead of building it. Build first,
  or start the whole stack, which finishes all builds before creating any container:

  ```bash
  docker compose -f docker-compose-non-dev.yml build superset-init
  ```

`COMPOSE_BAKE=false` is not a fix. The classic builder exports the image once per
service rather than once per mode, so it performs *more* duplicate writes of the same
tag, not fewer.

In mode 1, `superset-node` and `superset-websocket` keep their own `build:` blocks.
They build different stages under their own tags, so they were never part of the
collision.

`DEV_MODE=true` skips `npm ci` and `npm run build`, so the dev image ships an empty
`/app/superset/static/assets` — correct for mode 1, fatal for mode 2. When both
modes shared a tag, running `docker compose up` and then
`docker compose -f docker-compose-non-dev.yml up -d` silently reused the dev image
and every asset 404'd: the UI rendered as an unstyled page with a broken favicon,
while `/health` and the REST API kept returning 200. Mode 3 is isolated already by
its `name: lauretta-superset-secure` project name.

To confirm an image really has a bundle:

```bash
docker run --rm --entrypoint sh <tag> -c 'ls /app/superset/static/assets | wc -l'
# dev: 0 (expected). nondev and secure: ~800.
```

## Before pushing

```bash
git add <files>
pre-commit run          # or: pre-commit run --all-files
```

For the frontend gates that upstream CI runs, from clean build output, see "Steps"
in [08](08-upgrading-upstream.md).
