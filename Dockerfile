FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY knowledge/ knowledge/

RUN pip install --no-cache-dir .

ENTRYPOINT ["mcp-knowledge-server"]
