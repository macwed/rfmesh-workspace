# FreeTAKHub-WebMap — the separate "moving map" component that renders live CoT
# markers ("what the troops see"). Upstream ships NO Docker image; it publishes a
# compiled AMD64 Linux binary (a PyInstaller/pkg bundle of Node-RED 1.3.4 + the
# node-red-contrib-web-worldmap palette + the FreeTAKTeam TAK-map flow). We wrap
# that binary in a thin container.
#
# How it works at runtime:
#   FTH-webmap-linux-0.2.5 <path-to-webMAP_config.json>
# The bundled flow opens a TCP *client* connection to FreeTAKServer's CoT port
# (FTH_FTS_URL:FTH_FTS_TCP_Port, i.e. freetakserver:8087), parses the CoT XML
# stream, and plots each marker on a Leaflet worldmap served at /tak-map on
# container port 8000. It also talks to the FTS REST API (FTH_FTS_API_Port 19023,
# auth FTH_FTS_API_Auth) for registration. It holds NO history: markers exist on
# the map only while a live CoT for them is flowing (and fade with the CoT stale
# window). See deployment/webmap/README.md.
#
# Build context is deployment/webmap/ (see docker-compose.webmap.yml). The binary
# is fetched at build time from the upstream GitHub release so we do not vendor a
# 94 MB blob into the repo.

FROM debian:bookworm-slim

# Runtime libs the bundled Node binary links against (see `ldd`), plus curl/unzip
# to fetch the release at build time and ca-certificates for the HTTPS download.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip libstdc++6 zlib1g libffi8 \
    && rm -rf /var/lib/apt/lists/*

ARG FTH_WEBMAP_VERSION=0.2.5
ARG FTH_WEBMAP_URL=https://github.com/FreeTAKTeam/FreeTAKHub/releases/download/v0.2.5/FTH-webmap-linux-0.2.5.zip

WORKDIR /app

# Fetch + extract the compiled webmap binary. The zip contains the ELF binary and
# an example webMAP_config.json (we discard the example; our config is mounted).
RUN curl -fsSL -o /tmp/fth-webmap.zip "${FTH_WEBMAP_URL}" \
    && unzip -o /tmp/fth-webmap.zip -d /app \
    && rm -f /tmp/fth-webmap.zip /app/webMAP_config.json \
    && mv "/app/FTH-webmap-linux-${FTH_WEBMAP_VERSION}" /app/webmap \
    && chmod +x /app/webmap

# The webmap serves the Leaflet worldmap at /tak-map on this port.
EXPOSE 8000

# Config is bind-mounted by compose at /config/webMAP_config.json (read-only).
CMD ["/app/webmap", "/config/webMAP_config.json"]
