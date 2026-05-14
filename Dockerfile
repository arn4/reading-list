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
    PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "exec python app.py --host \"$HOST\" --port \"$PORT\" --database /data/database.json --auth-file /data/auth.json"]
