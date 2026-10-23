FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir --no-deps .
EXPOSE 8000 8001 8002
CMD ["uvicorn", "testpilot.web.api:app", "--host", "0.0.0.0", "--port", "8000"]
