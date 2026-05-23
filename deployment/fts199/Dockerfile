# FreeTAKServer 1.9.9.6 — the last stable pre-digitalpy (1.x) build.
#
# WHY THIS EXISTS
# ---------------
# `ghcr.io/freetakteam/freetakserver:latest` is the 2.x digitalpy rewrite
# (currently 2.2.1). That build does NOT reliably ingest marker-only CoT:
# it logs `IAMUsersController ... a bytes-like object is required, not
# 'NoneType'` on connection, drops repeat/new hostile-emitter markers, and
# only ever persists the very first marker. See deployment/README.md.
#
# No prebuilt 1.9.x Docker image survives on a public registry (Docker Hub
# `freetakteam/freetakserver` is deprecated/empty, GHCR carries only 2.x
# tags). So we build the proven 1.9.9.6 line ourselves from PyPI, using the
# base/recipe from the archived official FreeTAKServer-Docker Dockerfile
# (ubuntu:20.04 + python3.8 — 1.9.x is incompatible with py3.12/Cython 3.x).
#
# FTS 1.9.9.6 stores accepted CoT in /opt/fts/FTSServer-SQLDB.db (table
# `CoTTable`) when SaveCoTToDB is enabled, and accepts plain marker CoT over
# TCP :8087 with no presence handshake or IAM dance — which is exactly the
# behaviour the both3 backend's "Send to troops" needs.

FROM ubuntu:20.04

ARG FTS_VERSION=1.9.9.6
ENV DEBIAN_FRONTEND=noninteractive

# netbase is REQUIRED: it ships /etc/protocols + /etc/services. The minimal
# ubuntu:20.04 image omits both. FreeTAKServer 1.9.x imports eventlet, whose
# greendns shim imports dnspython's dns.rdtypes.IN.WKS at module load, which
# calls socket.getprotobyname('tcp'). Without /etc/protocols that raises
# `OSError: protocol not found` and the whole server crashes on startup before
# the CoT listener ever binds. Installing netbase fixes it cleanly.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev python3-setuptools python3-lxml \
        libssl-dev libffi-dev libxml2-dev libxslt-dev build-essential \
        iproute2 curl netbase \
    && rm -rf /var/lib/apt/lists/*

# Pin pip toolchain to a 1.9.x-era trio so the (unpinned, old) FTS deps
# resolve against metadata that still works with python3.8.
RUN python3 -m pip install --no-cache-dir "pip<24.1" "setuptools<66" "wheel<0.41"

# FreeTAKServer 1.9.9.x + the runtime deps the archived official image adds
# on top of the package's own requirements. We pin Flask-Login and pyOpenSSL
# to the exact versions FTS 1.9.9.6 requires (Flask-Login==0.5.0,
# pyOpenSSL==22.0.0) so pip does not pull incompatible newer wheels (the
# archived official Dockerfile left these unpinned, which now resolves to
# broken combos against current PyPI metadata).
RUN python3 -m pip install --no-cache-dir \
        "FreeTAKServer==${FTS_VERSION}" \
        requests defusedxml pytak \
        "Flask-Login==0.5.0" "pyOpenSSL==22.0.0"

# Persistent state (DB + config) lives here; mounted as a named volume.
RUN mkdir -p /opt/fts
WORKDIR /opt/fts

EXPOSE 8087 8089 19023

COPY start-fts.sh /start-fts.sh
RUN chmod +x /start-fts.sh

ENTRYPOINT ["/start-fts.sh"]
