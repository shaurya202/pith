"""The local API the window talks to.

Every edit is recomputed here. The window sends a threshold or a word
change and gets back the project plus the plan (what will be cut, and
the caption lines). It does not get to invent its own cut list.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pith import __version__
from pith.errors import UserError
from pith.export import export_video
from pith.media import audio_features, ffmpeg_version, probe, read_frames, scaled_frame_size
from pith.paths import fonts_dir, static_dir
from pith.plan import build_plan, public_plan
from pith.presets import CAPTION_FONTS, FONT_BOLD, PRESETS, Style, preset_style, public_presets
from pith.project import Store
from pith.ranges import clamp_ranges, snap_span
from pith.reference import analyze_frames
from pith.sample import ensure_reference_sample, ensure_talking_sample
from pith.transcribe import MODELS, transcribe_file

log = logging.getLogger("pith")

# One transcription at a time. Two Whisper models at once will happily
# use more memory than a laptop wants to give.
_transcribe_lock = threading.Lock()


class SettingsIn(BaseModel):
    pause_threshold: float | None = None
    padding: float | None = None
    aspect: str | None = None
    preset: str | None = None
    style: dict | None = None


class WordIn(BaseModel):
    text: str | None = None
    deleted: bool | None = None


class SpanIn(BaseModel):
    start: float
    end: float


class TranscribeIn(BaseModel):
    model: str = "base"
    language: str | None = None


class RenderIn(BaseModel):
    quality: str = "preview"


def _color(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != 7 or not text.startswith("#"):
        raise UserError("Colors need to look like #FFFFFF.")
    try:
        int(text[1:], 16)
    except ValueError as exc:
        raise UserError("Colors need to look like #FFFFFF.") from exc
    return text.upper()


def _validate_style(style: Style) -> Style:
    if style.font not in FONT_BOLD:
        raise UserError(f"Pick one of these fonts: {', '.join(CAPTION_FONTS)}.")
    style.bold = FONT_BOLD[style.font]
    style.font_size = int(style.font_size)
    if not 28 <= style.font_size <= 180:
        raise UserError("Font size needs to be between 28 and 180.")
    style.words_per_line = max(1, min(8, int(style.words_per_line)))
    if style.position not in {"top", "middle", "bottom"}:
        raise UserError("Position needs to be top, middle, or bottom.")
    if style.highlight_mode not in {"active", "cumulative", "none"}:
        style.highlight_mode = "active"
    style.text_color = _color(style.text_color)
    style.highlight_color = _color(style.highlight_color)
    style.uppercase = bool(style.uppercase)
    style.boxed = bool(style.boxed)
    return style


def _threshold(value: float) -> float:
    value = float(value)
    if value != value or value < 0.05 or value > 3:
        raise UserError("Pause threshold needs to be between 0.05 and 3 seconds.")
    return value


def _padding(value: float) -> float:
    value = float(value)
    if value != value or value < 0 or value > 0.5:
        raise UserError("Padding needs to be between 0 and 0.5 seconds.")
    return value


def _clean_text(text: str) -> str:
    collapsed = " ".join(str(text).replace("\n", " ").replace("\r", " ").split())
    if not collapsed:
        raise UserError("A caption word can't be empty.")
    return collapsed[:80]


def _busy(project) -> bool:
    job = project.job or {}
    return job.get("status") == "running" and not job.get("stale")


def create_app(data_dir: Path) -> FastAPI:
    store = Store(Path(data_dir))
    store.mark_jobs_stale()
    app = FastAPI(title="Pith", version=__version__)
    app.state.store = store

    @app.exception_handler(UserError)
    async def user_error(_request: Request, exc: UserError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    def payload(project):
        rms, hop = store.load_rms(project.id)
        plan = build_plan(project, rms, hop)
        return {"project": project.to_public(), "plan": public_plan(plan)}

    def require(project_id: str):
        return store.load(project_id)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/meta")
    def meta():
        try:
            ffmpeg = ffmpeg_version()
        except UserError as exc:
            ffmpeg = exc.message
        return {
            "name": "Pith",
            "version": __version__,
            "ffmpeg": ffmpeg,
            "fonts": CAPTION_FONTS,
            "presets": public_presets(),
            "models": list(MODELS),
            "languages": ["auto", "en", "es", "fr", "de", "pt", "hi", "ja", "ko", "zh"],
        }

    @app.get("/api/projects")
    def projects():
        return {"projects": store.list_projects()}

    @app.post("/api/projects")
    async def upload(file: UploadFile = File(...)):
        original = file.filename or "video.mp4"
        suffix = Path(original).suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v"}:
            raise UserError("Use an mp4 or mov file.")
        temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            with temporary:
                shutil.copyfileobj(file.file, temporary)
            path = Path(temporary.name)
            info = probe(path)
            if info["duration"] < 0.2:
                raise UserError("That clip is too short.")
            if info["duration"] > 30 * 60:
                raise UserError("Pith is built for short clips. This one is longer than 30 minutes.")
            if not info["has_audio"]:
                raise UserError("This video has no audio track, so there is nothing to transcribe.")
            peaks, rms, hop = audio_features(path)
            project = store.create(path, original, info, peaks)
            store.save_rms(project.id, rms, hop)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
        return payload(project)

    @app.post("/api/sample")
    def open_sample():
        video = ensure_talking_sample()
        info = probe(video)
        peaks, rms, hop = audio_features(video)
        project = store.create(video, "Pith sample.mp4", info, peaks)
        store.save_rms(project.id, rms, hop)
        return payload(project)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return payload(require(project_id))

    @app.get("/api/projects/{project_id}/plan")
    def get_plan(project_id: str):
        project = require(project_id)
        rms, hop = store.load_rms(project.id)
        return {"plan": public_plan(build_plan(project, rms, hop))}

    @app.post("/api/projects/{project_id}/settings")
    def settings(project_id: str, body: SettingsIn):
        with store.lock(project_id):
            project = require(project_id)
            if body.preset and not body.style:
                if body.preset not in PRESETS:
                    raise UserError(f"Unknown preset {body.preset!r}.")
                project.style = preset_style(body.preset)
            elif body.style:
                data = project.style.to_dict()
                for key, value in body.style.items():
                    if key in data and key != "preset":
                        data[key] = value
                data["preset"] = "custom"
                project.style = _validate_style(Style.from_dict(data))
            if body.pause_threshold is not None:
                project.pause_threshold = _threshold(body.pause_threshold)
            if body.padding is not None:
                project.padding = _padding(body.padding)
            if body.aspect is not None:
                if body.aspect not in {"original", "9:16"}:
                    raise UserError("Aspect needs to be original or 9:16.")
                project.aspect = body.aspect
            store.save(project)
        return payload(project)

    @app.post("/api/projects/{project_id}/words/{word_id}")
    def edit_word(project_id: str, word_id: int, body: WordIn):
        with store.lock(project_id):
            project = require(project_id)
            found = None
            for word in project.words:
                if word.id == word_id:
                    found = word
                    break
            if found is None:
                raise UserError("That word is not in the transcript.", 404)
            if body.text is not None:
                found.text = _clean_text(body.text)
            if body.deleted is not None:
                found.deleted = bool(body.deleted)
            store.save(project)
        return payload(project)

    def _edit_spans(project_id: str, body: SpanIn, kind: str, add: bool):
        with store.lock(project_id):
            project = require(project_id)
            start, end = float(body.start), float(body.end)
            if end < start:
                start, end = end, start
            clamped = clamp_ranges([(start, end)], project.duration)
            if not clamped:
                raise UserError("That stretch is outside the clip.")
            start, end = clamped[0]
            if end - start < 0.04:
                raise UserError("That stretch is too short to edit.")
            rms, hop = store.load_rms(project.id)
            plan = build_plan(project, rms, hop)
            if kind == "restore" and add:
                known = [(cut["start"], cut["end"]) for cut in plan["cuts"]]
            elif kind == "restore":
                known = [(item[0], item[1]) for item in project.restored]
            elif add:
                known = [(gap["start"], gap["end"]) for gap in plan["loud_gaps"]]
                known += [(item["start"], item["end"]) for item in plan["keeps"]]
            else:
                known = [(item[0], item[1]) for item in project.forced_cuts]
            start, end = snap_span((start, end), known)
            target = project.restored if kind == "restore" else project.forced_cuts
            if add:
                target.append([start, end])
            else:
                kept = []
                for item in target:
                    if not (item[1] > start and item[0] < end):
                        kept.append(item)
                if kind == "restore":
                    project.restored = kept
                else:
                    project.forced_cuts = kept
            store.save(project)
        return payload(project)

    @app.post("/api/projects/{project_id}/restore")
    def restore(project_id: str, body: SpanIn):
        return _edit_spans(project_id, body, "restore", True)

    @app.post("/api/projects/{project_id}/restore/remove")
    def unrestore(project_id: str, body: SpanIn):
        return _edit_spans(project_id, body, "restore", False)

    @app.post("/api/projects/{project_id}/force-cut")
    def force_cut(project_id: str, body: SpanIn):
        return _edit_spans(project_id, body, "force", True)

    @app.post("/api/projects/{project_id}/force-cut/remove")
    def unforce(project_id: str, body: SpanIn):
        return _edit_spans(project_id, body, "force", False)

    def _start_job(project_id: str, kind: str, work):
        with store.lock(project_id):
            project = require(project_id)
            if _busy(project):
                raise UserError("Pith is still working on this clip.", 409)
            project.job = {
                "kind": kind,
                "status": "running",
                "progress": 0.0,
                "message": "Starting",
                "error": "",
                "result": None,
            }
            store.save(project)

        def progress(fraction: float, message: str):
            with store.lock(project_id):
                current = store.load(project_id)
                if current.job and current.job.get("status") == "running":
                    current.job["progress"] = max(0.0, min(0.99, float(fraction)))
                    current.job["message"] = message
                    store.save(current)

        def runner():
            try:
                result = work(progress)
            except Exception as exc:
                log.exception("%s failed", kind)
                message = exc.message if isinstance(exc, UserError) else f"{exc.__class__.__name__}: {exc}"
                with store.lock(project_id):
                    current = store.load(project_id)
                    current.job = {
                        "kind": kind,
                        "status": "error",
                        "progress": 0,
                        "message": "",
                        "error": message,
                        "result": None,
                    }
                    store.save(current)
                return
            with store.lock(project_id):
                current = store.load(project_id)
                current.job = {
                    "kind": kind,
                    "status": "done",
                    "progress": 1,
                    "message": "Done",
                    "error": "",
                    "result": result,
                }
                store.save(current)

        threading.Thread(target=runner, name=f"pith-{kind}", daemon=True).start()
        return store.load(project_id)

    @app.post("/api/projects/{project_id}/transcribe")
    def transcribe(project_id: str, body: TranscribeIn):
        if body.model not in MODELS:
            raise UserError("Pick the tiny, base, or small model.")
        language = body.language or None
        if language == "auto":
            language = None

        def work(progress):
            if not _transcribe_lock.acquire(blocking=False):
                raise UserError("Another transcription is already running.", 409)
            try:
                with store.lock(project_id):
                    project = store.load(project_id)
                    source = store.source_path(project)
                    duration = project.duration
                words, detected = transcribe_file(
                    source,
                    duration,
                    model_name=body.model,
                    language=language,
                    on_progress=progress,
                )
                with store.lock(project_id):
                    current = store.load(project_id)
                    current.words = words
                    current.transcript_done = True
                    current.language = detected
                    # Old cut edits were about the previous timestamps.
                    current.restored = []
                    current.forced_cuts = []
                    store.save(current)
                return {"words": len(words), "language": detected}
            finally:
                _transcribe_lock.release()

        project = _start_job(project_id, "transcribe", work)
        return payload(project)

    def _analyze_reference(project_id: str, path: Path, label: str):
        with store.lock(project_id):
            project = require(project_id)
            info = probe(path)
            width, height = scaled_frame_size(info["width"], info["height"], 160)
            sample_fps = 2.0
            frames = read_frames(path, width, height, sample_fps, max_frames=24)
            # Only the first minute informs the guess. A long reference
            # would otherwise be sampled too thinly.
            duration = min(float(info["duration"]), 60.0)
            analysis = analyze_frames(frames, sample_fps, duration)
            analysis["filename"] = label
            project.reference = analysis
            store.save(project)
        return payload(project)

    @app.post("/api/projects/{project_id}/reference")
    async def upload_reference(project_id: str, file: UploadFile = File(...)):
        require(project_id)
        original = file.filename or "reference.mp4"
        suffix = Path(original).suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v"}:
            raise UserError("Use an mp4 or mov file for the reference.")
        temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            with temporary:
                shutil.copyfileobj(file.file, temporary)
            return _analyze_reference(project_id, Path(temporary.name), Path(original).name)
        finally:
            Path(temporary.name).unlink(missing_ok=True)

    @app.post("/api/projects/{project_id}/reference/sample")
    def sample_reference(project_id: str):
        require(project_id)
        return _analyze_reference(project_id, ensure_reference_sample(), "Sample reference")

    @app.post("/api/projects/{project_id}/reference/apply")
    def apply_reference(project_id: str):
        with store.lock(project_id):
            project = require(project_id)
            if not project.reference or "suggestion" not in project.reference:
                raise UserError("Match a reference first.")
            suggestion = project.reference["suggestion"]
            data = project.style.to_dict()
            if suggestion.get("position"):
                data["position"] = suggestion["position"]
            if suggestion.get("text_color"):
                data["text_color"] = suggestion["text_color"]
            if suggestion.get("highlight_color"):
                data["highlight_color"] = suggestion["highlight_color"]
            if suggestion.get("words_per_line"):
                data["words_per_line"] = suggestion["words_per_line"]
            data["preset"] = "custom"
            project.style = _validate_style(Style.from_dict(data))
            if suggestion.get("pause_threshold") is not None:
                project.pause_threshold = _threshold(suggestion["pause_threshold"])
            if suggestion.get("padding") is not None:
                project.padding = _padding(suggestion["padding"])
            store.save(project)
        return payload(project)

    @app.post("/api/projects/{project_id}/render")
    def render(project_id: str, body: RenderIn):
        quality = body.quality if body.quality in {"preview", "full"} else "preview"

        def work(progress):
            with store.lock(project_id):
                project = store.load(project_id)
                if not project.transcript_done:
                    raise UserError("Transcribe the clip before exporting.")
                source = store.source_path(project)
                style = project.style
                aspect = project.aspect
                display = (project.width, project.height)
            rms, hop = store.load_rms(project_id)
            plan = build_plan(project, rms, hop)
            folder = store.directory(project_id)
            filename = "preview.mp4" if quality == "preview" else "export.mp4"
            output = folder / filename
            summary = export_video(
                source,
                output,
                plan["_keeps"],
                plan["_spoken"],
                style,
                aspect=aspect,
                display_size=display,
                quality=quality,
                on_progress=progress,
            )
            summary["url"] = f"/api/projects/{project_id}/media/{'preview' if quality == 'preview' else 'export'}"
            return summary

        project = _start_job(project_id, "render", work)
        return payload(project)

    @app.get("/api/projects/{project_id}/media/{kind}")
    def media(project_id: str, kind: str, download: int = 0):
        project = require(project_id)
        folder = store.directory(project_id)
        if kind == "source":
            path = store.source_path(project)
            name = project.source_name
        elif kind == "preview":
            path = folder / "preview.mp4"
            name = f"{project.name}-preview.mp4"
        elif kind == "export":
            path = folder / "export.mp4"
            name = f"{project.name}.mp4"
        else:
            raise UserError("Unknown media.", 404)
        if not path.exists():
            raise UserError("That file has not been rendered yet.", 404)
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=name if download else None,
        )

    @app.get("/")
    def index():
        page = static_dir() / "index.html"
        return FileResponse(page)

    app.mount("/static", StaticFiles(directory=static_dir()), name="static")
    app.mount("/fonts", StaticFiles(directory=fonts_dir()), name="fonts")
    return app
