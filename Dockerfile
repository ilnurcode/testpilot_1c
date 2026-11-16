FROM node:22-bookworm-slim AS codex
RUN npm install --prefix /opt/codex --include=optional --no-audit --no-fund @openai/codex@0.167.2

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /opt/codex /opt/codex
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /opt/codex/node_modules/.bin/codex /usr/local/bin/codex
COPY . .
RUN pip install --no-cache-dir --no-deps .
EXPOSE 8000 8001 8002
CMD ["uvicorn", "testpilot.web.api:app", "--host", "0.0.0.0", "--port", "8000"]
