# Image for the three justdavis monitoring exporters (amplifi-exporter, gateway-exporter,
# airplay-exporter). One image, three entry points; Compose picks the command per service.
FROM python:3.13.13-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install locked dependencies first so source changes do not invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# Compose overrides the user with the host's `monitoring` uid; this keeps the image safe on its own.
RUN groupadd --system exporter && useradd --system --gid exporter --no-create-home exporter
USER exporter

# Exec form so the Python process is PID 1 and receives SIGTERM directly.
CMD ["amplifi-exporter"]
