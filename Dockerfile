FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EXPENSES_DB=/data/accounts.db

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY database.py main.py web_app.py web_database.py ./
COPY static ./static

RUN useradd --system --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

USER appuser
EXPOSE 8000

CMD ["sh", "-c", "uvicorn web_app:app --host 0.0.0.0 --port ${PORT:-8000}"]
