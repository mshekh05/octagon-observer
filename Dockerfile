# --- build the PWA ---
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- python app: api, worker and CLI ---
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY octagon_edge ./octagon_edge
RUN pip install --no-cache-dir ".[server]"
COPY --from=web /web/dist ./frontend/dist
COPY scripts ./scripts
COPY examples ./examples
ENV PYTHONUNBUFFERED=1 FRONTEND_DIST=/app/frontend/dist
VOLUME ["/app/data", "/app/reports"]
EXPOSE 8000
CMD ["uvicorn", "octagon_edge.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
