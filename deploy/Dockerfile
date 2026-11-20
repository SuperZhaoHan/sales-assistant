FROM python:3.15-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build
RUN pip install --no-cache-dir build

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /build/dist


FROM python:3.15-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    APP_CONFIG_DIR=/app/config

RUN groupadd --system --gid 999 app \
    && useradd --system --uid 999 --gid app --create-home app

WORKDIR /app
COPY --from=builder /build/dist/*.whl /tmp/
ARG EXTRAS="anthropic,redis,otel"
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[${EXTRAS}]" \
    && rm -rf /tmp/*.whl

COPY config ./config

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"

CMD ["python", "-m", "uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
