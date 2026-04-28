FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/bridge

COPY bridge/pyproject.toml /app/bridge/pyproject.toml
COPY bridge/README.md /app/bridge/README.md
COPY bridge/bridge /app/bridge/bridge

RUN pip install /app/bridge

CMD ["wb-bridge", "run-loop"]
