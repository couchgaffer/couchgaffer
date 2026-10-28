# syntax=docker/dockerfile:1

FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/watchparty ./watchparty
RUN uv sync --locked --no-dev --no-editable

# The recap job (#15): the app's code plus Pillow, ffmpeg and a font. Build with --target recap.
FROM build AS recap-build
RUN uv sync --locked --no-dev --group recap --no-editable

FROM python:3.12-slim AS recap
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=recap-build /app/.venv ./.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001
CMD ["python", "-m", "watchparty.recap.worker"]

# The app: the default target, so it stays last.
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY --from=web /web/dist ./static
ENV PATH="/app/.venv/bin:$PATH" STATIC_DIR=/app/static PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
# Screens never send data: frames over 4 KB are refused, not buffered up to the 16 MiB default (#50)
CMD ["uvicorn", "watchparty.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", \
     "--ws-max-size", "4096"]
