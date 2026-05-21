# both3-poc image. Build context = WORKSPACE ROOT (so we can reach the two
# governed packages). Build from the workspace root, e.g.:
#   docker build -f deployment/Dockerfile --target dev -t both3-poc:dev .
# Compose sets context: .. for you.
#
# Stages: base -> builder (wheels) -> runtime (prod) -> dev (hot-reload).

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# ---- builder: produce wheels for everything ----
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# The two governed packages, copied read-only from the build context.
COPY packages/rfmesh-contracts /src/rfmesh-contracts
COPY packages/rfmesh-cot       /src/rfmesh-cot

# Third-party deps first (so the local --no-deps wheels resolve against them).
RUN pip wheel --wheel-dir /wheels \
        fastapi "uvicorn[standard]" "pydantic>=2.11" "numpy>=2.1" \
        "cryptography>=42" "pytak>=6,<7"
# Local packages: contracts then cot, --no-deps (deps already wheeled above).
RUN pip wheel --no-deps --wheel-dir /wheels /src/rfmesh-contracts /src/rfmesh-cot
# MGRS is display-only and may lack a wheel on some platforms; best-effort.
# (mgrs imports `packaging` at runtime, so wheel it too.)
RUN pip wheel --wheel-dir /wheels mgrs packaging || echo "WARN: mgrs wheel unavailable; MGRS display disabled"

# ---- runtime: prod image, code baked in, no source bind mounts ----
FROM base AS runtime
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels \
        fastapi "uvicorn[standard]" rfmesh-contracts rfmesh-cot
RUN pip install --no-index --find-links=/wheels mgrs packaging \
        || echo "WARN: mgrs not installed; MGRS display disabled"
RUN rm -rf /wheels

ENV PYTHONPATH=/app/backend/src \
    FRONTEND_DIR=/app/frontend \
    SEED_FILE=/app/seed/demo_fixes.json \
    SEED_BEARINGS_FILE=/app/seed/demo_bearings.json
COPY deployment/backend/src  /app/backend/src
COPY deployment/frontend     /app/frontend
COPY deployment/backend/seed /app/seed

EXPOSE 8000
CMD ["uvicorn", "both3_poc.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- dev: hot-reload (source is bind-mounted over /app/backend/src) ----
FROM runtime AS dev
ENV WATCHFILES_FORCE_POLLING=true
CMD ["uvicorn", "both3_poc.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app/backend/src"]
