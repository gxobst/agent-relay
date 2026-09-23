# Agent Relay — local container image.
#
# Only the app modules are copied (never .venv, git history, or *.db files).
# uvicorn must bind 0.0.0.0 inside the container, otherwise `-p` publishing
# looks broken because uvicorn defaults to 127.0.0.1 (container loopback).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/agent-relay

# Runtime deps mirror pyproject.toml (no dev/test deps in the image).
RUN pip install --no-cache-dir \
    "fastapi>=0.141.1" \
    "uvicorn[standard]>=0.52.4" \
    "sqlalchemy>=2.0.52" \
    "pydantic-settings>=2.15.0" \
    "psycopg[binary]>=3.3.5"

COPY main.py database.py storage.py schemas.py errors.py worker.py dashboard.py dashboard.html ./

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
