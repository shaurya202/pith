"""Local transcription with faster-whisper.

The model is downloaded once into the Hugging Face cache and reused.
After that, a clip never leaves this computer. `tiny` is the fast model,
`base` is the default, `small` is slower and a bit clearer. All of them
run on the CPU so a laptop without a supported GPU still works.
"""

from __future__ import annotations

import logging
import threading

from pith.errors import UserError
from pith.project import Word

log = logging.getLogger("pith")

MODELS = ("tiny", "base", "small")
_models: dict = {}
_lock = threading.Lock()
_pyav_ready = False


def _patch_pyav() -> None:
    """Keep faster-whisper working on current PyAV.

    faster-whisper opens audio with av.open(..., metadata_errors="ignore").
    PyAV 19 dropped that argument, and av.open is a builtin so there is no
    signature to inspect. Try the call as written, and if this PyAV rejects
    the argument, open the file without it.
    """

    global _pyav_ready
    if _pyav_ready:
        return
    import av

    original = av.open

    def open_compat(*args, **kwargs):
        if "metadata_errors" not in kwargs:
            return original(*args, **kwargs)
        try:
            return original(*args, **kwargs)
        except TypeError as exc:
            if "metadata_errors" not in str(exc):
                raise
            kwargs.pop("metadata_errors", None)
            return original(*args, **kwargs)

    av.open = open_compat
    _pyav_ready = True


def _clock(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def load_model(name: str):
    if name not in MODELS:
        raise UserError(f"Unknown model {name!r}. Use tiny, base, or small.")
    with _lock:
        if name in _models:
            return _models[name]
        log.info("loading whisper model %s", name)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise UserError(
                "faster-whisper is not installed. Run pip install -r requirements.txt and try again."
            ) from exc
        try:
            model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=0)
        except Exception as exc:
            log.warning("int8 model load failed (%s); trying float32", exc)
            try:
                model = WhisperModel(name, device="cpu", compute_type="float32", cpu_threads=0)
            except Exception as second:
                raise UserError(
                    "Couldn't load the Whisper model. The first run downloads it "
                    "(tiny is about 75 MB, base about 150 MB, small about 500 MB) "
                    "and needs a connection that one time. "
                    f"({second.__class__.__name__}: {second})"
                ) from second
        _models[name] = model
        return model


def _punctuation(text: str) -> bool:
    return not any(character.isalnum() for character in text)


def transcribe_file(path, duration: float, model_name: str = "base", language: str | None = None, on_progress=None):
    """Return (words, language code).

    Word text has the leading space Whisper adds stripped off. A token that
    is only punctuation is glued to the word before it, so a caption does
    not get its own beat for a comma.
    """

    model = load_model(model_name)
    _patch_pyav()
    chosen = language or None
    if chosen == "auto":
        chosen = None
    try:
        segments, info = model.transcribe(
            str(path),
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            language=chosen,
            condition_on_previous_text=False,
            vad_parameters={"min_silence_duration_ms": 250},
        )
    except TypeError:
        # Older faster-whisper builds use a different vad argument shape.
        segments, info = model.transcribe(
            str(path),
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            language=chosen,
            condition_on_previous_text=False,
        )
    except Exception as exc:
        raise UserError(
            "Couldn't transcribe this clip. If this is the first run, Pith needs "
            "a one-time download of the Whisper model. Check your connection and try again. "
            f"({exc.__class__.__name__}: {exc})"
        ) from exc

    words: list[Word] = []
    next_id = 1
    for segment in segments:
        if on_progress and duration:
            on_progress(min(0.98, float(segment.end) / duration), f"Heard up to {_clock(segment.end)}")
        for token in segment.words or []:
            text = (token.word or "").strip()
            if not text:
                continue
            start = float(token.start)
            end = float(token.end)
            if end < start:
                start, end = end, start
            if end <= start:
                end = start + 0.05
            probability = float(getattr(token, "probability", 1.0) or 0)
            if _punctuation(text) and words:
                words[-1].text = (words[-1].text + text).strip()
                words[-1].end = max(words[-1].end, end)
                continue
            if len(text) > 80:
                text = text[:80]
            words.append(
                Word(
                    id=next_id,
                    text=text,
                    start=start,
                    end=end,
                    probability=probability,
                )
            )
            next_id += 1
    language_code = getattr(info, "language", None)
    log.info("transcribed %s words, language %s", len(words), language_code)
    if on_progress:
        on_progress(1.0, f"{len(words)} words")
    return words, language_code
