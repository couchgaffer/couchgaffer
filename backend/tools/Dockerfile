# In-region latency test (#6): runs tools.latency_test next to the models, with managed identity.
# Build from the repo root:  az acr build -r <registry> -t latency-test:<tag> -f backend/tools/Dockerfile .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/tools ./tools
COPY contracts/examples /contracts/examples
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
ENTRYPOINT ["/app/.venv/bin/python", "-m", "tools.latency_test"]
