FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py auth.py metadata.py ./
COPY static ./static

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app

ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATABASE_PATH=/data/database.json \
    AUTH_FILE_PATH=/data/auth.json

EXPOSE 8000

CMD ["sh", "-c", "python app.py --host \"$HOST\" --port \"$PORT\" --database \"$DATABASE_PATH\" --auth-file \"$AUTH_FILE_PATH\""]
