# Reading List v0.2

A local priority-queue reading list. Push links, pairwise-compare to order them,
read the top of the queue, rate what you finish.

## Install

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```sh
python app.py                                # http://127.0.0.1:8000
python app.py --port 9000                    # different port
python app.py --host 0.0.0.0 --port 8080     # bind to all interfaces
python app.py --database ~/reading.json      # custom database path
python app.py --auth-file ~/rl-auth.json     # custom auth file path
python app.py --workers 1                    # required: single worker only
```

Then open the printed URL in a browser.

## Storage

Everything lives in a single JSON file — `database.json` next to `app.py` by
default, or whatever you pass with `--database`. Safe to inspect, back up, or
hand-edit while the server is stopped.

## Authentication

Single-user, passkey-only. On the first visit, the app forces you to set up a
passkey. From then on, every visit requires authenticating with that passkey.

All authentication state — the user id, the registered passkey, and active
session tokens — lives in `auth.json` next to `app.py` (override with
`--auth-file`). To reset auth (e.g. lost device, want a new passkey), stop the
server, delete that file, and start the server again — you'll be back at the
"set up a passkey" screen.

The session cookie lifetime is **1 day**.

Requires a recent browser that supports the WebAuthn JSON helpers
(`PublicKeyCredential.parseCreationOptionsFromJSON` etc.) — Chrome 121+,
Safari 17.4+, Firefox 122+.

> **Passkeys require a secure context.** WebAuthn only runs over
> `http://localhost` or `https://<your-domain>`. If you expose the app on a
> public host without HTTPS, the browser will refuse to create or use the
> passkey and **authentication will simply not work** — there is no fallback.

## Deploying behind a reverse proxy (HTTPS)

The expected production setup is a TLS-terminating reverse proxy (Caddy,
nginx, Traefik, …) in front of the app. The proxy handles HTTPS; the app
listens on plain HTTP locally and trusts `X-Forwarded-*` headers from any
upstream (assume the app is firewalled to the proxy only).

This app currently must run with exactly one worker process (`--workers 1`)
because pending WebAuthn challenges and file-write locking are process-local.
Do not run `uvicorn --workers >1` for this build.

To tell the app it's running behind HTTPS — which makes the session cookie
`Secure` — set the `USE_HTTPS` env var or pass `--https`:

```sh
python app.py --https                 # explicit flag
USE_HTTPS=1 python app.py             # via env var
python app.py --no-https              # explicitly disable
```

The default (HTTPS off) is the right choice for local development on
`http://localhost`. Turn it on in your `.env` for any public deployment.

Built-in endpoint rate limits:
- `POST /auth/login/begin`: default 10 requests per 60 seconds per client IP
- `POST /links/prepare`: default 20 requests per 60 seconds per client IP

You can tune these with:
- `RATE_LIMIT_WINDOW_SECONDS`
- `RATE_LIMIT_AUTH_LOGIN_BEGIN`
- `RATE_LIMIT_LINKS_PREPARE`

Minimal Caddyfile:

```
reading.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy obtains a Let's Encrypt certificate automatically.

Minimal nginx server block:

```
server {
    listen 443 ssl;
    server_name reading.example.com;
    ssl_certificate     /etc/letsencrypt/live/reading.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/reading.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## How priority works

When you add a link, the server walks you through ~log₂(N) "A vs B"
comparisons to binary-search its slot in the queue. The **≈ Roughly equal**
button stops early and places the new item at the current midpoint.

The **Bump** button on a queued item runs the same flow against the rest of
the queue, so you can re-prioritize without deleting + re-adding.

## Endpoints

| Method | Path                       | Purpose                              |
|--------|----------------------------|--------------------------------------|
| GET    | `/`                        | UI                                   |
| POST   | `/links/prepare`           | Fetch title + summary for a URL      |
| POST   | `/links/insert/start`      | Begin insertion, get first compare   |
| POST   | `/links/insert/step`       | Submit a compare; get next or done   |
| GET    | `/links/top?k=10`          | Top-K of the queue                   |
| GET    | `/links/queue/count`       | Total queue size                     |
| POST   | `/links/{id}/read`         | Move to read list; rating 1..5 or null |
| POST   | `/links/{id}/rating`       | Set/clear rating on a read item      |
| POST   | `/links/{id}/move`         | Move a queued item up or down by one |
| POST   | `/links/{id}/bump`         | Re-prioritize a queued item          |
| DELETE | `/links/{id}`              | Remove from queue or read list       |
| GET    | `/links/read`              | Read list                            |
