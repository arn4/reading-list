import argparse
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from metadata import fetch_metadata

VERSION = "0.1"
ROOT = Path(__file__).parent
DATA_FILE = ROOT / "database.json"
STATIC_DIR = ROOT / "static"

_lock = threading.Lock()


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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.post("/links/prepare")
def prepare(body: PrepareBody):
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
    global DATA_FILE
    p = argparse.ArgumentParser(description=f"Reading List v{VERSION}")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    p.add_argument(
        "--database",
        default=str(DATA_FILE),
        help="path to JSON database file (default: database.json)",
    )
    args = p.parse_args()
    DATA_FILE = Path(args.database).expanduser().resolve()
    import uvicorn
    print(f"Reading List v{VERSION} — http://{args.host}:{args.port} — db: {DATA_FILE}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
