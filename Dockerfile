# The Finance Dashboard as a container, so any host can run it (SPEC F7.3).
#
#   docker build -t finance-dashboard .
#   docker run -p 8000:8000 --env-file .env -v finance-data:/app/data finance-dashboard
#
# Secrets are never built in: pass them when starting (--env-file .env, or the host's own
# secret settings). The database lives in the /app/data volume, so it survives updates.
FROM python:3.12.11-slim-bookworm

# uv installs the exact library versions from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Libraries first (this step is reused while uv.lock doesn't change), then the app.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY alembic.ini ./
COPY app ./app
COPY config ./config

# A user without admin rights runs the app; it may only write to /app/data.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown app:app /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL=sqlite:////app/data/app.db \
    FORWARDED_ALLOW_IPS=127.0.0.1

VOLUME /app/data
USER app
EXPOSE 8000

# Bring the database up to date, then start the web server. --proxy-headers lets the app
# see that a visitor came over HTTPS when an HTTPS proxy (e.g. Cloudflare's tunnel) at one
# of FORWARDED_ALLOW_IPS forwards the request; headers from anyone else are ignored.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips $FORWARDED_ALLOW_IPS"]
