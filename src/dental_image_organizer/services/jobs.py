"""Background jobs on a shared executor (spec §4.3): per-key ordering and versioned progress."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

import structlog
from structlog.contextvars import bound_contextvars

from dental_image_organizer.errors import DioError, JobNotFoundError
from dental_image_organizer.storage.schema import new_id

log = structlog.get_logger(__name__)

JobState = Literal["queued", "running", "done", "failed"]

_INTERNAL_MESSAGE = "An unexpected error occurred; see the log file."


@dataclass(frozen=True)
class JobError:
    code: str
    message: str


@dataclass(frozen=True)
class JobSnapshot:
    id: str
    kind: str
    key: str | None
    state: JobState
    done: int = 0
    total: int = 0
    message: str | None = None
    result: Mapping[str, Any] | None = None
    error: JobError | None = None
    version: int = 1

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed")


class JobProgress(Protocol):
    def __call__(self, done: int, total: int, message: str | None = None) -> None: ...


JobFn = Callable[[JobProgress], Mapping[str, Any] | None]


class JobManager:
    def __init__(
        self,
        executor: Executor,
        *,
        keep_finished: int = 200,
        on_finish: Callable[[JobSnapshot], None] | None = None,
    ) -> None:
        self._executor = executor
        self._keep_finished = keep_finished
        self._on_finish = on_finish
        # One lock guards every field below; the condition lets `wait` sleep on it.
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._jobs: dict[str, JobSnapshot] = {}
        self._fns: dict[str, JobFn] = {}
        self._finished: deque[str] = deque()
        # Keys with a job handed to the executor; later jobs of the key wait in `_waiting`.
        self._busy: set[str] = set()
        self._waiting: dict[str, deque[str]] = {}

    def submit(
        self, kind: str, fn: JobFn, *, key: str | None = None, total: int = 0
    ) -> JobSnapshot:
        job_id = new_id()
        snapshot = JobSnapshot(id=job_id, kind=kind, key=key, state="queued", total=total)
        with self._lock:
            self._jobs[job_id] = snapshot
            self._fns[job_id] = fn
            start_now = key is None or key not in self._busy
            if key is None:
                pass
            elif start_now:
                self._busy.add(key)
            else:
                self._waiting.setdefault(key, deque()).append(job_id)
        if start_now:
            self._executor.submit(self._run, job_id)
        return snapshot

    def get(self, job_id: str) -> JobSnapshot:
        with self._lock:
            return self._get_locked(job_id)

    def active(self, key: str | None = None) -> bool:
        with self._lock:
            return any(
                not job.finished and (key is None or job.key == key) for job in self._jobs.values()
            )

    def active_any(self, keys: Iterable[str]) -> bool:
        wanted = set(keys)
        with self._lock:
            return any(not job.finished and job.key in wanted for job in self._jobs.values())

    def wait(self, job_id: str, timeout: float | None = None) -> JobSnapshot:
        with self._changed:
            if not self._changed.wait_for(
                lambda: self._get_locked(job_id).finished, timeout=timeout
            ):
                raise TimeoutError(f"Job {job_id} did not finish in time.")
            return self._get_locked(job_id)

    async def watch(self, job_id: str, *, interval: float = 0.1) -> AsyncIterator[JobSnapshot]:
        # Polling with asyncio.sleep keeps the event loop free; the worker threads never touch it.
        last = 0
        while True:
            snapshot = self.get(job_id)
            if snapshot.version > last:
                last = snapshot.version
                yield snapshot
            if snapshot.finished:
                return
            await asyncio.sleep(interval)

    def cancel_queued(self) -> int:
        cancelled: list[JobSnapshot] = []
        with self._lock:
            for queue in self._waiting.values():
                while queue:
                    job_id = queue.popleft()
                    self._fns.pop(job_id, None)
                    snapshot = self._jobs[job_id]
                    self._jobs[job_id] = replace(
                        snapshot,
                        state="failed",
                        error=JobError("cancelled", "The app was closing."),
                        version=snapshot.version + 1,
                    )
                    self._remember_finished(job_id)
                    cancelled.append(self._jobs[job_id])
            self._waiting.clear()
            self._changed.notify_all()
        for snapshot in cancelled:
            self._notify_finished(snapshot)
        return len(cancelled)

    def _get_locked(self, job_id: str) -> JobSnapshot:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFoundError(f"Unknown job {job_id}.") from None

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            snapshot = self._jobs[job_id]
            self._jobs[job_id] = replace(snapshot, version=snapshot.version + 1, **changes)
            self._changed.notify_all()

    def _remember_finished(self, job_id: str) -> None:
        # Caller holds the lock.
        self._finished.append(job_id)
        while len(self._finished) > self._keep_finished:
            self._jobs.pop(self._finished.popleft(), None)

    def _notify_finished(self, snapshot: JobSnapshot) -> None:
        if self._on_finish is None:
            return
        try:
            self._on_finish(snapshot)
        except Exception:
            log.exception("job_on_finish_failed", job_id=snapshot.id)

    def _progress(self, job_id: str) -> JobProgress:
        def report(done: int, total: int, message: str | None = None) -> None:
            self._update(job_id, done=done, total=total, message=message)

        return report

    def _run(self, job_id: str) -> None:
        with self._lock:
            fn = self._fns.pop(job_id)
            kind = self._jobs[job_id].kind
        self._update(job_id, state="running")
        with bound_contextvars(job_id=job_id, job_kind=kind):
            outcome: dict[str, Any] = {"state": "done"}
            try:
                result = fn(self._progress(job_id))
                outcome["result"] = dict(result) if result is not None else None
            except DioError as exc:
                # The cause may be an OSError holding a patient name, so only the code is logged.
                log.warning("job_failed", code=exc.code)
                outcome = {"state": "failed", "error": JobError(exc.code, str(exc))}
            except OSError as exc:
                reason = exc.strerror or type(exc).__name__
                log.warning("job_failed", error=reason)
                outcome = {"state": "failed", "error": JobError("io_error", reason)}
            except Exception:
                log.exception("job_failed")
                outcome = {
                    "state": "failed",
                    "error": JobError("internal_error", _INTERNAL_MESSAGE),
                }
            self._finish(job_id, outcome)

    def _finish(self, job_id: str, outcome: dict[str, Any]) -> None:
        with self._lock:
            snapshot = self._jobs[job_id]
            final = replace(snapshot, version=snapshot.version + 1, **outcome)
        # Hooks run before the job is published as finished, so a waiter sees their effects.
        self._notify_finished(final)
        next_id: str | None = None
        with self._lock:
            self._jobs[job_id] = final
            self._remember_finished(job_id)
            if final.key is not None:
                queue = self._waiting.get(final.key)
                if queue:
                    next_id = queue.popleft()
                else:
                    self._waiting.pop(final.key, None)
                    self._busy.discard(final.key)
            self._changed.notify_all()
        # Handing over instead of blocking keeps a worker from idling for its key's turn.
        if next_id is not None:
            self._executor.submit(self._run, next_id)
