FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/parser

# Install deps first for better Docker layer caching.
COPY parser/pyproject.toml /app/parser/pyproject.toml
COPY parser/README.md /app/parser/README.md
COPY parser/app /app/parser/app
COPY configs /app/configs

RUN pip install /app/parser

EXPOSE 8000

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
