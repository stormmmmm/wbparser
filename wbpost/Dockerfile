FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY wbpost/pyproject.toml ./pyproject.toml
COPY wbpost/wbpost ./wbpost

RUN pip install --upgrade pip setuptools wheel \
 && pip install .

# admin.yml is bind-mounted at runtime; data dir too.
WORKDIR /workspace

ENTRYPOINT ["wbpost"]
CMD ["--help"]
