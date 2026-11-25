# raib-ha supmeta (Superset metadata) database image: Docker Hardened Image (DHI) postgres 18,
# Debian 13, CIS profile, no pgvector. Pinned by digest (the linux/amd64 manifest of
# dhi.io/postgres:18 exported 2026-11-13). The other five raib-ha DBs use the database repo's
# ha/Dockerfile (DHI pgvector); both need the same layout and the same fixed settings.
# Plus pgAudit, built like the database repo's (845bd3c on) against THIS base's headers, and
# object auditing of reads of the audit table (pgaudit.conf here; raib-ha D104).
#
# Build (the fixed settings come from the database repo, never copied here; it must be at
# 845bd3c or later: ha.conf includes pgaudit.conf, and its pgaudit.conf is this one's base):
#   sudo docker build --build-context hadb=<database checkout>/ha \
#     --build-arg DATABASE_COMMIT=$(git -C <database checkout> rev-parse --short=12 HEAD) \
#     -t raib-ha/supmeta-pg18:<REV> docker/ha/supmeta
# Offline (no docker login dhi.io): also
#   --build-context dhi.io/postgres@<FROM digest>=oci-layout://<dir>@<FROM digest>
# The pgAudit stage needs internet (Debian packages, the GitHub tarball): build in the lab,
# carry the image.
FROM dhi.io/postgres@sha256:aed6224a248aa5167d07c7c41fa4b3037abf1087571ed0a1093a38aee93ee1b0 AS base

# pgAudit 18.1 (tag commit 91b7db030d44c1b5585f851de1923eeec1b2d625, 2026-11-17), the release
# for PostgreSQL 18; the source tarball is pinned by its sha256 (the same pin as the database
# repo's ha/Dockerfile). Built on Debian 13 (the base's distro, glibc 2.41) with this base's own
# pg_config, server headers and PGXS (PostgreSQL 18.7). The base has no compiler. No LLVM
# bitcode (with_llvm=no).
FROM debian@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0 AS pgaudit
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc make libc6-dev libssl-dev libkrb5-dev \
 && rm -rf /var/lib/apt/lists/*
COPY --from=base /usr/bin/pg_config /usr/bin/pg_config
COPY --from=base /usr/lib/postgresql/18/include /usr/lib/postgresql/18/include
COPY --from=base /usr/lib/postgresql/18/lib/pgxs /usr/lib/postgresql/18/lib/pgxs
ADD --checksum=sha256:f585ef3c741d7bad322b69c9c917b9ed79c800f15460f585be4cc96ef3f8ce0f \
    https://github.com/pgaudit/pgaudit/archive/refs/tags/18.1.tar.gz /pgaudit.tar.gz
RUN mkdir /src && tar -xzf /pgaudit.tar.gz -C /src --strip-components=1 \
 && make -C /src USE_PGXS=1 with_llvm=no \
 && make -C /src USE_PGXS=1 with_llvm=no install DESTDIR=/out

FROM base

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

COPY --from=pgaudit --chmod=0755 /out/usr/lib/postgresql/18/lib/pgaudit.so /usr/lib/postgresql/18/lib/
COPY --from=pgaudit --chmod=0644 /out/usr/lib/postgresql/18/share/extension/pgaudit* /usr/lib/postgresql/18/share/extension/
COPY --from=hadb --chmod=0644 postgresql.ha.conf /etc/postgresql/ha.conf
# ha.conf includes pgaudit.conf (supmeta's, below), which includes the database repo's settings.
COPY --from=hadb --chmod=0644 pgaudit.conf /etc/postgresql/pgaudit.base.conf
COPY --chmod=0644 pgaudit.conf /etc/postgresql/pgaudit.conf
