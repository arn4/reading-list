import json

import app


def test_startup_migration_backfills_missing_fields(tmp_path, monkeypatch):
    db = tmp_path / "database.json"
    db.write_text(
        json.dumps(
            {
                "queue": [{"id": "q1", "url": "https://q", "title": "Queued"}],
                "read": [{"id": "r1", "url": "https://r", "title": "Read", "read_at": "now"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "DATA_FILE", db)

    app.run_startup_migrations()
    data = app.load_data()
    assert data["queue"][0]["summary"] == ""
    assert data["read"][0]["summary"] == ""
    assert data["read"][0]["notes"] == ""

    persisted = json.loads(db.read_text(encoding="utf-8"))
    assert persisted["read"][0]["notes"] == ""


def test_top_uses_env_default_when_k_is_not_provided(monkeypatch):
    monkeypatch.setattr(app, "QUEUE_TOP_K_DEFAULT", 2)
    monkeypatch.setattr(
        app,
        "load_data",
        lambda: {"queue": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "read": []},
    )

    assert len(app.top()) == 2
    assert len(app.top(k=1)) == 1


def test_update_notes_on_read_item(tmp_path, monkeypatch):
    db = tmp_path / "database.json"
    db.write_text(
        json.dumps(
            {
                "queue": [],
                "read": [
                    {
                        "id": "r1",
                        "url": "https://r",
                        "title": "Read",
                        "read_at": "now",
                        "rating": None,
                        "summary": "",
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "DATA_FILE", db)

    updated = app.update_notes("r1", app.NotesBody(notes="Great references section"))
    assert updated["notes"] == "Great references section"

    persisted = json.loads(db.read_text(encoding="utf-8"))
    assert persisted["read"][0]["notes"] == "Great references section"
