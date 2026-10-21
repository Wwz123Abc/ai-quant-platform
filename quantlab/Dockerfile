# 1. Build the web workbench
FROM node:24-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# 2. The service, with the built workbench inside the package
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUANTLAB_DATA_DIR=/data \
    QUANTLAB_REPORTS_DIR=/data/reports \
    QUANTLAB_HOST=0.0.0.0
RUN useradd --create-home --uid 10001 quantlab && mkdir /data && chown quantlab /data
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=web /app/src/quantlab/web ./src/quantlab/web
RUN pip install --no-cache-dir . && rm -rf src
USER quantlab
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["quantlab", "serve"]
