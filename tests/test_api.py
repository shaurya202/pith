"""The window's API: upload, edit a word, and see the cut plan change."""

from pathlib import Path

from fastapi.testclient import TestClient

from pith.project import Word
from pith.server import create_app
from tests.helpers import make_video


def test_upload_edit_and_plan(tmp_path):
    app = create_app(tmp_path / "data")
    client = TestClient(app)
    video = make_video(tmp_path / "clip.mp4", seconds=3.0, size="320x240")
    with video.open("rb") as handle:
        response = client.post("/api/projects", files={"file": ("clip.mp4", handle, "video/mp4")})
    assert response.status_code == 200, response.text
    body = response.json()
    project_id = body["project"]["id"]
    assert body["plan"]["ready"] is False
    assert body["project"]["has_audio"] is True

    store = app.state.store
    project = store.load(project_id)
    project.transcript_done = True
    project.words = [
        Word(1, "hello", 0.2, 0.6),
        Word(2, "there", 2.0, 2.5),
    ]
    project.pause_threshold = 0.4
    project.padding = 0.0
    store.save(project)

    plan = client.get(f"/api/projects/{project_id}/plan").json()["plan"]
    assert plan["stats"]["cut_count"] >= 1
    assert plan["stats"]["cut_seconds"] > 0.5

    edited = client.post(
        f"/api/projects/{project_id}/words/1",
        json={"text": "hey", "deleted": False},
    )
    assert edited.status_code == 200, edited.text
    words = {word["id"]: word for word in edited.json()["plan"]["words"]}
    assert words[1]["text"] == "hey"

    removed = client.post(f"/api/projects/{project_id}/words/2", json={"deleted": True})
    assert removed.status_code == 200
    included = [word for word in removed.json()["plan"]["words"] if word["included"]]
    assert [word["text"] for word in included] == ["hey"]

    styled = client.post(f"/api/projects/{project_id}/settings", json={"preset": "karaoke"})
    assert styled.status_code == 200
    assert styled.json()["project"]["style"]["preset"] == "karaoke"
    assert styled.json()["project"]["style"]["uppercase"] is False

    bad = client.post("/api/projects", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert bad.status_code == 400


def test_home_page_is_the_app(tmp_path):
    client = TestClient(create_app(tmp_path / "data"))
    page = client.get("/")
    assert page.status_code == 200
    assert "Cut the pauses" in page.text
    assert "/static/app.js" in page.text
    client = TestClient(create_app(tmp_path / "data"))
    assert client.get("/api/health").json()["ok"] is True
    meta = client.get("/api/meta").json()
    assert meta["name"] == "Pith"
    assert "hormozi" in {item["preset"] for item in meta["presets"]}
    assert Path(meta["ffmpeg"]) or "ffmpeg" in meta["ffmpeg"].lower()
