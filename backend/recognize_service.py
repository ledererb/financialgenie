"""
Recognition service: thin async wrapper around the existing
src/ai/field_recognizer.py FieldRecognizer so the editor can trigger AI
re-recognition without touching the filling pipeline.

Recognition can take 30-120s on large PDFs, so it runs on a background
thread and the frontend polls GET /api/recognize/{task_id}/status.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config import log

try:
    from ai.field_recognizer import FieldRecognizer, MappingConfig  # type: ignore
    _HAVE_RECOGNIZER = True
except Exception as e:  # pragma: no cover - defensive
    log.warning("FieldRecognizer import failed: %s", e)
    _HAVE_RECOGNIZER = False
    FieldRecognizer = None  # type: ignore
    MappingConfig = None  # type: ignore


@dataclass
class TaskState:
    task_id: str
    pdf_id: str
    status: str = "pending"  # pending|running|done|error
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: dict | None = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None


class RecognitionService:
    def __init__(self):
        self._tasks: dict[str, TaskState] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return _HAVE_RECOGNIZER

    def start(self, pdf_path: Path, pdf_id: str, mode: str) -> str:
        if not _HAVE_RECOGNIZER:
            raise RuntimeError("FieldRecognizer not available (missing deps)")
        task_id = uuid.uuid4().hex[:12]
        state = TaskState(task_id=task_id, pdf_id=pdf_id)
        with self._lock:
            self._tasks[task_id] = state
        t = threading.Thread(target=self._run, args=(state, pdf_path, mode), daemon=True)
        t.start()
        return task_id

    def status(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def _run(self, state: TaskState, pdf_path: Path, mode: str):
        try:
            state.status = "running"
            state.message = "Starting FieldRecognizer..."

            recognizer = FieldRecognizer()

            state.progress = 0.1
            state.message = f"Recognizing ({mode})..."

            # Progress callback: called by FieldRecognizer after each batch.
            # progress_val is 0.0–1.0 (fraction of batches done).
            def on_batch_progress(batch_num: int, total_batches: int, page_label: str):
                if total_batches > 0:
                    # Reserve 0.1–0.9 range for actual recognition
                    state.progress = 0.1 + 0.8 * (batch_num / total_batches)
                state.message = f"Batch {batch_num}/{total_batches} ({page_label})"

            recognizer.progress_callback = on_batch_progress

            # Map editor's mode names to the recognizer's mode names.
            rec_mode = "overlay" if mode == "flat" else mode
            mapping_cfg = recognizer.recognize(pdf_path, mode=rec_mode)

            state.progress = 0.9
            state.message = "Serializing result..."

            data = mapping_cfg.to_dict() if isinstance(mapping_cfg, MappingConfig) else mapping_cfg
            state.result = data

            # Save mapping to disk automatically
            try:
                from mapping_service import mapping_service
                mapping_service.save(state.pdf_id, data)
                log.info("Automatically saved recognized mapping for %s to disk", state.pdf_id)
            except Exception as save_err:
                log.error("Failed to auto-save recognized mapping: %s", save_err)

            state.status = "done"
            state.progress = 1.0
            state.finished_at = datetime.now().isoformat(timespec="seconds")
            state.message = f"Done: {len(data.get('fields', []))} fields"
        except Exception as e:
            log.exception("recognition failed")
            state.status = "error"
            state.error = str(e)
            state.finished_at = datetime.now().isoformat(timespec="seconds")


recognize_service = RecognitionService()
