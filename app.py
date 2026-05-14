import argparse
import json
import os
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import SESSION_COOKIE, AuthStore
from metadata import fetch_metadata

VERSION = "0.2"
ROOT = Path(__file__).parent
DATA_FILE = ROOT / "database.json"
AUTH_FILE = ROOT / "auth.json"
STATIC_DIR = ROOT / "static"

TRUTHY = {"1", "true", "yes", "on"}
HTTPS_ENABLED: bool = os.environ.get("USE_HTTPS", "").lower() in TRUTHY
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24  # 1 day
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_AUTH_LOGIN_BEGIN = int(os.environ.get("RATE_LIMIT_AUTH_LOGIN_BEGIN", "10"))
RATE_LIMIT_LINKS_PREPARE = int(os.environ.get("RATE_LIMIT_LINKS_PREPARE", "20"))

auth_store: AuthStore = AuthStore(AUTH_FILE)

_lock = threading.Lock()
_rate_limit_lock = threading.Lock()
_rate_limit_hits: Dict[Tuple[str, str], Deque[float]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_data() -> Dict[str, List[Dict[str, Any]]]:
    if not DATA_FILE.exists():
        return {"queue": [], "read": []}
    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: Dict[str, Any]) -> None:
    tmp = DATA_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


class PrepareBody(BaseModel):
    url: str


class Candidate(BaseModel):
    url: str
    title: str
    summary: str = ""
    id: Optional[str] = None
    added_at: Optional[str] = None


class StartBody(BaseModel):
    url: str
    title: str
    summary: str = ""


class State(BaseModel):
    lo: int
    hi: int


class StepBody(BaseModel):
    state: State
    candidate: Candidate
    choice: str  # "new" | "existing" | "skip"


class RatingBody(BaseModel):
    rating: Optional[int] = None


class MoveBody(BaseModel):
    direction: str  # "up" | "down"


app = FastAPI(title="Reading List", version=VERSION)


PUBLIC_PATHS = {"/", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/", "/auth/")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)
    assert auth_store is not None
    token = request.cookies.get(SESSION_COOKIE)
    if not auth_store.has_session(token):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=HTTPS_ENABLED,
        samesite="lax",
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
    )


def _client_ip(request: Request) -> str:
    client = request.client
    if client is None:
        return "unknown"
    return client.host


def _enforce_rate_limit(request: Request, scope: str, limit: int) -> None:
    if limit <= 0:
        return
    now = monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    key = (scope, _client_ip(request))
    with _rate_limit_lock:
        bucket = _rate_limit_hits.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded for {scope}: max {limit} requests/{RATE_LIMIT_WINDOW_SECONDS}s",
            )
        bucket.append(now)


@app.get("/auth/status")
def auth_status(request: Request):
    assert auth_store is not None
    token = request.cookies.get(SESSION_COOKIE)
    return {
        "registered": auth_store.is_registered(),
        "authenticated": auth_store.has_session(token),
    }


@app.post("/auth/register/begin")
def auth_register_begin(request: Request):
    assert auth_store is not None
    return Response(content=auth_store.begin_registration(request), media_type="application/json")


@app.post("/auth/register/complete")
async def auth_register_complete(request: Request):
    assert auth_store is not None
    credential = await request.json()
    token = auth_store.complete_registration(request, credential)
    resp = JSONResponse({"ok": True})
    _set_session_cookie(resp, token)
    return resp


@app.post("/auth/login/begin")
def auth_login_begin(request: Request):
    assert auth_store is not None
    _enforce_rate_limit(request, "auth/login/begin", RATE_LIMIT_AUTH_LOGIN_BEGIN)
    return Response(content=auth_store.begin_authentication(request), media_type="application/json")


@app.post("/auth/login/complete")
async def auth_login_complete(request: Request):
    assert auth_store is not None
    credential = await request.json()
    token = auth_store.complete_authentication(request, credential)
    resp = JSONResponse({"ok": True})
    _set_session_cookie(resp, token)
    return resp


@app.post("/auth/logout")
def auth_logout(request: Request):
    assert auth_store is not None
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth_store.revoke_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.post("/links/prepare")
def prepare(request: Request, body: PrepareBody):
    _enforce_rate_limit(request, "links/prepare", RATE_LIMIT_LINKS_PREPARE)
    meta = fetch_metadata(body.url)
    return {"url": body.url, "title": meta["title"], "summary": meta["summary"]}


def _next_comparison(queue: List[Dict[str, Any]], candidate: Dict[str, Any], lo: int, hi: int):
    mid = (lo + hi) // 2
    return {
        "done": False,
        "state": {"lo": lo, "hi": hi},
        "candidate": candidate,
        "compare_against": queue[mid],
        "mid": mid,
    }


def _insert_at(queue: List[Dict[str, Any]], candidate: Dict[str, Any], position: int) -> Dict[str, Any]:
    item = {
        "id": candidate.get("id") or str(uuid.uuid4()),
        "url": candidate["url"],
        "title": candidate["title"],
        "summary": candidate.get("summary", ""),
        "added_at": candidate.get("added_at") or now_iso(),
    }
    queue.insert(position, item)
    return item


@app.post("/links/insert/start")
def insert_start(body: StartBody):
    candidate = {
        "url": body.url,
        "title": body.title,
        "summary": body.summary,
    }
    with _lock:
        data = load_data()
        queue = data["queue"]
        if not queue:
            item = _insert_at(queue, candidate, 0)
            save_data(data)
            return {"done": True, "position": 0, "item": item}
        n = len(queue)
        return _next_comparison(queue, candidate, 0, n)


@app.post("/links/insert/step")
def insert_step(body: StepBody):
    lo = body.state.lo
    hi = body.state.hi
    candidate = body.candidate.model_dump()

    with _lock:
        data = load_data()
        queue = data["queue"]

        if lo < 0 or hi > len(queue) or lo > hi:
            raise HTTPException(400, "stale comparison state — queue changed")

        mid = (lo + hi) // 2

        if body.choice == "skip":
            position = mid
        elif body.choice == "new":
            hi = mid
            if lo >= hi:
                position = lo
            else:
                return _next_comparison(queue, candidate, lo, hi)
        elif body.choice == "existing":
            lo = mid + 1
            if lo >= hi:
                position = lo
            else:
                return _next_comparison(queue, candidate, lo, hi)
        else:
            raise HTTPException(400, f"unknown choice: {body.choice}")

        item = _insert_at(queue, candidate, position)
        save_data(data)
        return {"done": True, "position": position, "item": item}


@app.get("/links/top")
def top(k: int = 10):
    data = load_data()
    return data["queue"][:k]


@app.get("/links/queue/count")
def queue_count():
    data = load_data()
    return {"count": len(data["queue"])}


@app.post("/links/{link_id}/read")
def mark_read(link_id: str, body: RatingBody):
    if body.rating is not None and (body.rating < 1 or body.rating > 5):
        raise HTTPException(400, "rating must be 1..5 or null")
    with _lock:
        data = load_data()
        idx = next((i for i, x in enumerate(data["queue"]) if x["id"] == link_id), None)
        if idx is None:
            raise HTTPException(404, "not in queue")
        item = data["queue"].pop(idx)
        item["read_at"] = now_iso()
        item["rating"] = body.rating
        data["read"].append(item)
        save_data(data)
    return item


@app.post("/links/{link_id}/rating")
def update_rating(link_id: str, body: RatingBody):
    if body.rating is not None and (body.rating < 1 or body.rating > 5):
        raise HTTPException(400, "rating must be 1..5 or null")
    with _lock:
        data = load_data()
        idx = next((i for i, x in enumerate(data["read"]) if x["id"] == link_id), None)
        if idx is None:
            raise HTTPException(404, "not in read list")
        data["read"][idx]["rating"] = body.rating
        save_data(data)
        return data["read"][idx]


@app.post("/links/{link_id}/move")
def move(link_id: str, body: MoveBody):
    if body.direction not in ("up", "down"):
        raise HTTPException(400, "direction must be 'up' or 'down'")
    with _lock:
        data = load_data()
        queue = data["queue"]
        idx = next((i for i, x in enumerate(queue) if x["id"] == link_id), None)
        if idx is None:
            raise HTTPException(404, "not in queue")
        if body.direction == "up":
            if idx == 0:
                raise HTTPException(400, "already at top")
            queue[idx - 1], queue[idx] = queue[idx], queue[idx - 1]
            new_idx = idx - 1
        else:
            if idx == len(queue) - 1:
                raise HTTPException(400, "already at bottom")
            queue[idx + 1], queue[idx] = queue[idx], queue[idx + 1]
            new_idx = idx + 1
        save_data(data)
        return {"ok": True, "position": new_idx}


@app.post("/links/{link_id}/bump")
def bump(link_id: str):
    with _lock:
        data = load_data()
        idx = next((i for i, x in enumerate(data["queue"]) if x["id"] == link_id), None)
        if idx is None:
            raise HTTPException(404, "not in queue")
        item = data["queue"].pop(idx)
        save_data(data)
        queue = data["queue"]
        candidate = {
            "id": item["id"],
            "url": item["url"],
            "title": item["title"],
            "summary": item.get("summary", ""),
            "added_at": item.get("added_at"),
        }
        if not queue:
            # only item — re-insert at 0 immediately
            _insert_at(queue, candidate, 0)
            save_data(data)
            return {"done": True, "position": 0, "item": candidate}
        return _next_comparison(queue, candidate, 0, len(queue))


@app.delete("/links/{link_id}")
def delete_link(link_id: str):
    with _lock:
        data = load_data()
        for bucket in ("queue", "read"):
            idx = next((i for i, x in enumerate(data[bucket]) if x["id"] == link_id), None)
            if idx is not None:
                data[bucket].pop(idx)
                save_data(data)
                return {"deleted": True, "bucket": bucket}
        raise HTTPException(404, "not found")


@app.get("/links/read")
def read_list():
    data = load_data()
    return data["read"]


@app.get("/version")
def version():
    return {"version": VERSION}


def main():
    global DATA_FILE, AUTH_FILE, auth_store, HTTPS_ENABLED
    p = argparse.ArgumentParser(description=f"Reading List v{VERSION}")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    p.add_argument(
        "--database",
        default=str(DATA_FILE),
        help="path to JSON database file (default: database.json)",
    )
    p.add_argument(
        "--auth-file",
        default=str(AUTH_FILE),
        help="path to passkey/auth file (default: auth.json). Delete this file to reset auth.",
    )
    p.add_argument(
        "--https",
        action=argparse.BooleanOptionalAction,
        default=HTTPS_ENABLED,
        help="treat the deployment as HTTPS — sets the session cookie Secure flag. "
             "Default reads from the USE_HTTPS env var (truthy values: 1/true/yes/on).",
    )
    default_workers = int(os.environ.get("WEB_CONCURRENCY", os.environ.get("UVICORN_WORKERS", "1")))
    p.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help="number of worker processes. Must be 1 for this app (default: 1).",
    )
    args = p.parse_args()
    if args.workers != 1:
        p.error(
            "This app currently requires exactly one worker because pending WebAuthn challenges "
            "and locks are process-local. Use --workers 1."
        )
    DATA_FILE = Path(args.database).expanduser().resolve()
    AUTH_FILE = Path(args.auth_file).expanduser().resolve()
    HTTPS_ENABLED = args.https
    auth_store = AuthStore(AUTH_FILE)
    import uvicorn
    display_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0", "::1", "::") else args.host
    print(
        f"Reading List v{VERSION} — open http://{display_host}:{args.port}\n"
        f"  (WebAuthn requires a domain name — do not use an IP address)\n"
        f"  https: {'on (cookies marked Secure)' if HTTPS_ENABLED else 'off'}\n"
        f"  session cookie max-age: {SESSION_MAX_AGE_SECONDS // 3600}h\n"
        f"  workers: {args.workers}\n"
        f"  db:    {DATA_FILE}\n"
        f"  auth:  {AUTH_FILE} ({'registered' if auth_store.is_registered() else 'not registered yet'})"
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=args.workers,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
