FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/maxapi

COPY maxapi/pyproject.toml /app/maxapi/pyproject.toml
COPY maxapi/README.md /app/maxapi/README.md
COPY maxapi/api /app/maxapi/api
COPY maxapi/openapi.yml /app/maxapi/openapi.yml

RUN pip install "/app/maxapi[pymax]"

EXPOSE 8080

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
