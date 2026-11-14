FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARCHITECT_TENANT=hmc-makerspace \
    ARCHITECT_TENANT_DIR=/app/tenant_packs \
    DATABASE_URL=sqlite:////data/architect_mcp.db \
    PORT=8765

COPY pyproject.toml README.md ./
COPY architect_mcp ./architect_mcp
COPY tenant_packs ./tenant_packs

RUN mkdir -p /data && pip install --no-cache-dir .

EXPOSE 8765

CMD ["architect-mcp", "--transport", "streamable-http", "--host", "0.0.0.0"]
