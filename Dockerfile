# ---------------- Python build stage ----------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS python-build-stage

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential \
  libpq-dev

# Install dependencies (cached)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . ${APP_HOME}

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev


# ---------------- Python run stage ----------------
FROM python:3.12-slim-bookworm AS python-run-stage

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

RUN addgroup --system django && adduser --system --ingroup django django

# Install runtime dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  libpq-dev \
  gettext \
  wait-for-it \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# Copy entrypoint and start scripts
COPY --chown=django:django ./entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//g' /entrypoint.sh && chmod +x /entrypoint.sh

COPY --chown=django:django ./compose/production/django/start /start
RUN sed -i 's/\r$//g' /start && chmod +x /start

COPY --chown=django:django ./compose/production/django/celery/worker/start /start-celeryworker
RUN sed -i 's/\r$//g' /start-celeryworker && chmod +x /start-celeryworker

COPY --chown=django:django ./compose/production/django/celery/beat/start /start-celerybeat
RUN sed -i 's/\r$//g' /start-celerybeat && chmod +x /start-celerybeat

COPY --chown=django:django ./compose/production/django/celery/flower/start /start-flower
RUN sed -i 's/\r$//g' /start-flower && chmod +x /start-flower

# Copy application from builder
COPY --from=python-build-stage --chown=django:django ${APP_HOME} ${APP_HOME}

# Ensure django owns working dir
RUN chown django:django ${APP_HOME}

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Drop privileges
USER django

CMD ["/entrypoint.sh"]
