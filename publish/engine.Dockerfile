FROM python:3.14.6-slim@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/uv-python
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY libs/engine-contract ./libs/engine-contract
COPY libs/pdf-translate/pyproject.toml libs/pdf-translate/LICENSE libs/pdf-translate/README.md ./libs/pdf-translate/
COPY libs/pdf-translate/pdf_translate ./libs/pdf-translate/pdf_translate
COPY libs/pdf-translate/provenance/release.json ./libs/pdf-translate/provenance/release.json
COPY apps/engine/pyproject.toml apps/engine/uv.lock apps/engine/.python-version ./apps/engine/
RUN cd apps/engine && uv sync --frozen --no-dev --no-install-project
COPY apps/engine/pdft_engine ./apps/engine/pdft_engine
RUN cd apps/engine && uv sync --frozen --no-dev
COPY tools/ci/verify_pinned_library_commit.py ./tools/ci/verify_pinned_library_commit.py
COPY tools/ci/library_provenance.py ./tools/ci/library_provenance.py
RUN python3 tools/ci/verify_pinned_library_commit.py apps/engine
# Generated tail (tools/engine-export/templates/engine.Dockerfile.tail). Everything above is the engine's build
# recipe, copied verbatim; this tail starts the engine alone, over a Unix socket. It never binds a TCP port.
COPY publish/ ./publish/
ENV ENGINE_ADDRESS=unix:/tmp/pdft-sock/engine.sock \
    APP_CALLBACK_ADDRESS=unix:/tmp/pdft-sock/app-callback.sock
WORKDIR /app/apps/engine
CMD ["sh", "-c", "mkdir -p -m 0700 /tmp/pdft-sock && exec /app/apps/engine/.venv/bin/uvicorn pdft_engine.server:app --uds /tmp/pdft-sock/engine.sock --no-access-log"]
