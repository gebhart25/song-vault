# Song Vault: one image with the FastAPI backend, the built SvelteKit app and ffmpeg.
# Build it from a clone of the repository; see "Self-hosting" in README.md.

# --- 1. Build the frontend ------------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. Runtime -------------------------------------------------------------------
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
# Dependencies first so code changes don't reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

COPY --from=frontend /src/frontend/build /app/static

ENV PATH="/app/backend/.venv/bin:$PATH" \
    SONGVAULT_DATA_DIR=/data \
    SONGVAULT_MEDIA_DIR=/media \
    SONGVAULT_STATIC_DIR=/app/static \
    PYTHONUNBUFFERED=1

# Runs as UID 99 / GID 100 (nobody:users, the Unraid convention, so files on SMB shares
# stay accessible). Pass `--user` to docker run to use another account.
RUN mkdir -p /data /media && chown 99:100 /data /media
USER 99:100

EXPOSE 8095
VOLUME ["/data", "/media"]
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/api/health', timeout=4)"

# --proxy-headers lets the app see HTTPS from a reverse proxy (for Secure cookies).
CMD ["uvicorn", "--factory", "songvault.main:create_app", "--host", "0.0.0.0", "--port", "8095", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
