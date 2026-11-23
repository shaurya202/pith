"""A project is one video and every edit made to it.

Projects live as JSON under the data directory, next to the copied video
and a small numpy file of the volume envelope. JSON is the whole format
on purpose: you can open project.json in a text editor and see the words.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pith.errors import UserError
from pith.presets import Style, preset_style


def _now() -> float:
    return time.time()


@dataclass
class Word:
    id: int
    text: str
    start: float
    end: float
    deleted: bool = False
    probability: float = 1.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "deleted": bool(self.deleted),
            "probability": round(float(self.probability), 3),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Word":
        return cls(
            id=int(data["id"]),
            text=str(data.get("text", "")),
            start=float(data.get("start", 0)),
            end=float(data.get("end", 0)),
            deleted=bool(data.get("deleted", False)),
            probability=float(data.get("probability", 1)),
        )


@dataclass
class Project:
    id: str
    name: str
    source_name: str
    source_ext: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    created: float
    updated: float
    words: list[Word] = field(default_factory=list)
    transcript_done: bool = False
    language: str | None = None
    pause_threshold: float = 0.40
    padding: float = 0.08
    restored: list[list[float]] = field(default_factory=list)
    forced_cuts: list[list[float]] = field(default_factory=list)
    style: Style = field(default_factory=lambda: preset_style("hormozi"))
    aspect: str = "9:16"
    peaks: list[float] = field(default_factory=list)
    reference: dict | None = None
    job: dict | None = None

    def touch(self) -> None:
        self.updated = _now()

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "id": self.id,
            "name": self.name,
            "source_name": self.source_name,
            "source_ext": self.source_ext,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "has_audio": self.has_audio,
            "created": self.created,
            "updated": self.updated,
            "words": [word.to_dict() for word in self.words],
            "transcript_done": self.transcript_done,
            "language": self.language,
            "pause_threshold": self.pause_threshold,
            "padding": self.padding,
            "restored": self.restored,
            "forced_cuts": self.forced_cuts,
            "style": self.style.to_dict(),
            "aspect": self.aspect,
            "peaks": self.peaks,
            "reference": self.reference,
            "job": self.job,
        }

    def to_public(self) -> dict:
        """What the window is allowed to see. No filesystem paths."""

        data = self.to_dict()
        data["media"] = {
            "source": f"/api/projects/{self.id}/media/source",
            "preview": f"/api/projects/{self.id}/media/preview",
            "export": f"/api/projects/{self.id}/media/export",
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        words = [Word.from_dict(item) for item in data.get("words") or []]
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or "Untitled"),
            source_name=str(data.get("source_name") or "video"),
            source_ext=str(data.get("source_ext") or ".mp4"),
            duration=float(data.get("duration") or 0),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            fps=float(data.get("fps") or 30),
            has_audio=bool(data.get("has_audio", True)),
            created=float(data.get("created") or _now()),
            updated=float(data.get("updated") or _now()),
            words=words,
            transcript_done=bool(data.get("transcript_done", False)),
            language=data.get("language"),
            pause_threshold=float(data.get("pause_threshold", 0.40)),
            padding=float(data.get("padding", 0.08)),
            restored=list(data.get("restored") or []),
            forced_cuts=list(data.get("forced_cuts") or []),
            style=Style.from_dict(data.get("style")),
            aspect=str(data.get("aspect") or "9:16"),
            peaks=list(data.get("peaks") or []),
            reference=data.get("reference"),
            job=data.get("job"),
        )


class Store:
    """One folder per project, plus a lock so a render and a slider don't collide."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock(self, project_id: str) -> threading.Lock:
        with self._guard:
            if project_id not in self._locks:
                self._locks[project_id] = threading.Lock()
            return self._locks[project_id]

    def _check_id(self, project_id: str) -> str:
        if not isinstance(project_id, str) or len(project_id) != 12 or not all(
            character in "0123456789abcdef" for character in project_id
        ):
            raise UserError("Unknown project.", 404)
        return project_id

    def directory(self, project_id: str) -> Path:
        project_id = self._check_id(project_id)
        path = (self.root / project_id).resolve()
        if path.parent != self.root.resolve():
            raise UserError("Unknown project.", 404)
        return path

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def create(self, source: Path, original_name: str, info: dict, peaks: list[float]) -> Project:
        project_id = self.new_id()
        folder = self.root / project_id
        folder.mkdir(parents=True)
        ext = source.suffix.lower() if source.suffix else ".mp4"
        if ext not in {".mp4", ".mov", ".m4v"}:
            ext = ".mp4"
        target = folder / f"source{ext}"
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        now = _now()
        stem = Path(original_name).stem or "Untitled"
        project = Project(
            id=project_id,
            name=stem[:80],
            source_name=Path(original_name).name[:180],
            source_ext=ext,
            duration=float(info["duration"]),
            width=int(info["width"]),
            height=int(info["height"]),
            fps=float(info["fps"]),
            has_audio=bool(info["has_audio"]),
            created=now,
            updated=now,
            peaks=peaks,
        )
        self.save(project)
        return project

    def save(self, project: Project) -> None:
        project.touch()
        folder = self.directory(project.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "project.json"
        temporary = folder / "project.json.tmp"
        temporary.write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(path)

    def load(self, project_id: str) -> Project:
        path = self.directory(project_id) / "project.json"
        if not path.exists():
            raise UserError("That project is not on this computer anymore.", 404)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UserError("This project file is damaged and could not be read.", 500) from exc
        project = Project.from_dict(data)
        # A render that was running when the app closed will never finish.
        if project.job and project.job.get("status") == "running" and project.job.get("stale"):
            project.job["status"] = "error"
            project.job["error"] = "Pith closed before this finished. Run it again."
        return project

    def source_path(self, project: Project) -> Path:
        return self.directory(project.id) / f"source{project.source_ext}"

    def rms_path(self, project_id: str) -> Path:
        return self.directory(project_id) / "rms.npy"

    def save_rms(self, project_id: str, rms: np.ndarray, hop: float) -> None:
        if rms is None or len(rms) == 0:
            return
        np.savez(self.directory(project_id) / "audio.npz", rms=rms.astype(np.float32), hop=np.float32(hop))

    def load_rms(self, project_id: str):
        path = self.directory(project_id) / "audio.npz"
        if not path.exists():
            return None, 0.02
        data = np.load(path)
        return data["rms"], float(data["hop"])

    def list_projects(self, limit: int = 8) -> list[dict]:
        items = []
        if not self.root.exists():
            return items
        for folder in self.root.iterdir():
            manifest = folder / "project.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append(
                {
                    "id": data.get("id"),
                    "name": data.get("name") or "Untitled",
                    "source_name": data.get("source_name") or "",
                    "duration": data.get("duration") or 0,
                    "updated": data.get("updated") or 0,
                    "transcript_done": bool(data.get("transcript_done")),
                }
            )
        items.sort(key=lambda item: item["updated"], reverse=True)
        return items[:limit]

    def mark_jobs_stale(self) -> None:
        """Call once at startup. A 'running' job in a file is already dead."""

        for folder in self.root.iterdir() if self.root.exists() else []:
            manifest = folder / "project.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job = data.get("job")
            if not job or job.get("status") != "running":
                continue
            job["stale"] = True
            manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
