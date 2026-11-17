# raib-ha supmeta (Superset metadata) database image: Docker Hardened Image (DHI) postgres 18,
# Debian 13, CIS profile, no pgvector. Pinned by digest (the linux/amd64 manifest of
# dhi.io/postgres:18 exported 2026-11-13). The other five raib-ha DBs use the database repo's
# ha/Dockerfile (DHI pgvector); both need the same layout and the same fixed settings.
#
# Build (the fixed settings come from the database repo, never copied here):
#   sudo docker build --build-context hadb=<database checkout>/ha \
#     --build-arg DATABASE_COMMIT=$(git -C <database checkout> rev-parse --short=12 HEAD) \
#     -t raib-ha/supmeta-pg18:<REV> docker/ha/supmeta
# Offline (no docker login dhi.io): also
#   --build-context dhi.io/postgres@<FROM digest>=oci-layout://<dir>@<FROM digest>
FROM dhi.io/postgres@sha256:aed6224a248aa5167d07c7c41fa4b3037abf1087571ed0a1093a38aee93ee1b0

ARG DATABASE_COMMIT=unknown
LABEL raib-ha.database-commit=$DATABASE_COMMIT

# raib-ha's layout (database repo ha/README.md): data volume at /var/lib/postgresql, PGDATA in
# 18/docker owned by postgres (uid 70; a fresh volume copies the image's root-owned 18/), socket in
# /var/run/postgresql (DHI has none; its libpq defaults to /tmp, hence PGHOST), /etc/postgresql
# 0755 (COPY --chmod would create it 0644). DHI's passwd has no root entry: numeric user.
USER 0
RUN install -d -o 70 -g 70 -m 0700 /var/lib/postgresql/18/docker \
 && install -d -o 70 -g 70 -m 0755 /var/run/postgresql \
 && install -d -m 0755 /etc/postgresql
USER 70
ENV PGDATA=/var/lib/postgresql/18/docker
ENV PGHOST=/var/run/postgresql
VOLUME /var/lib/postgresql

COPY --from=hadb --chmod=0644 postgresql.ha.conf /etc/postgresql/ha.conf
