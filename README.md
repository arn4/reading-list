# Reading List v0.2.1

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

In Docker, the default command stores auth at `/data/auth.json`, so it persists
in the same mounted volume as `database.json`.

Requires a recent browser that supports the WebAuthn JSON helpers
(`PublicKeyCredential.parseCreationOptionsFromJSON` etc.) — Chrome 121+,
Safari 17.4+, Firefox 122+. On localhost the app works over plain HTTP; on any
other host you must serve it over HTTPS for WebAuthn to function.

## Docker Hub CI

GitHub Actions now builds and pushes a multi-arch image (`linux/amd64`,
`linux/arm64`) on every push.

Set these repo secrets before enabling it:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN` (Docker Hub access token)

Optional repo variable:

- `DOCKERHUB_REPOSITORY` (for example `larna/reading-list`; if omitted, it uses
  `$DOCKERHUB_USERNAME/reading-list`)

Published tags:

- `latest`
- `v<app version>` (from `VERSION` in `app.py`; re-pushed each run, so an
  existing version tag is replaced)
- `<git sha>`

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
