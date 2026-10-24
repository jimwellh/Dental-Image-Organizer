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


_CANCELLED = JobError("cancelled", "The app was closing.")
_INTERNAL = JobError("internal_error", _INTERNAL_MESSAGE)


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
        self._closing = False

    def submit(
        self, kind: str, fn: JobFn, *, key: str | None = None, total: int = 0
    ) -> JobSnapshot:
        job_id = new_id()
        snapshot = JobSnapshot(id=job_id, kind=kind, key=key, state="queued", total=total)
        start_now = False
        with self._lock:
            self._jobs[job_id] = snapshot
            if self._closing:
                # A shut-down executor would raise; a finished job keeps `wait` and `watch` honest.
                cancelled = self._fail_locked(job_id, _CANCELLED)
            else:
                cancelled = None
                self._fns[job_id] = fn
                start_now = key is None or key not in self._busy
                if key is None:
                    pass
                elif start_now:
                    self._busy.add(key)
                else:
                    self._waiting.setdefault(key, deque()).append(job_id)
        if cancelled is not None:
            self._notify_finished(cancelled)
            return cancelled
        if start_now:
            self._dispatch(job_id)
        return snapshot

    def get(self, job_id: str) -> JobSnapshot:
        with self._lock:
            return self._get_locked(job_id)

    def active(self, key: str | None = None) -> bool:
        with self._lock:
            return any(
                not job.finished and (key is None or job.key == key) for job in self._jobs.values()
            )

    def active_job(self, key: str) -> JobSnapshot | None:
        """The newest queued or running job of `key`, so a repeated request can name it."""
        with self._lock:
            # Dicts keep insertion order, so the last match is the newest submission.
            return next(
                (j for j in reversed(self._jobs.values()) if j.key == key and not j.finished),
                None,
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
        # Jobs sitting in the executor's own queue are `queued` too and would start (and block
        # `shutdown(wait=True)`), so every queued snapshot fails, and closing stops later handoffs.
        cancelled: list[JobSnapshot] = []
        with self._lock:
            self._closing = True
            for job_id, snapshot in list(self._jobs.items()):
                if snapshot.state == "queued":
                    cancelled.append(self._fail_locked(job_id, _CANCELLED))
            self._waiting.clear()
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

    def _fail_locked(self, job_id: str, error: JobError) -> JobSnapshot:
        # Caller holds the lock.
        self._fns.pop(job_id, None)
        snapshot = self._jobs[job_id]
        failed = replace(snapshot, state="failed", error=error, version=snapshot.version + 1)
        self._jobs[job_id] = failed
        self._remember_finished(job_id)
        self._changed.notify_all()
        return failed

    def _notify_finished(self, snapshot: JobSnapshot) -> None:
        if self._on_finish is None:
            return
        try:
            self._on_finish(snapshot)
        except OSError as exc:
            # The message of an OSError carries its path.
            log.warning(
                "job_on_finish_failed",
                job_id=snapshot.id,
                error=exc.strerror or type(exc).__name__,
            )
        except Exception:
            log.exception("job_on_finish_failed", job_id=snapshot.id)

    def _progress(self, job_id: str) -> JobProgress:
        def report(done: int, total: int, message: str | None = None) -> None:
            self._update(job_id, done=done, total=total, message=message)

        return report

    def _dispatch(self, job_id: str) -> None:
        try:
            self._executor.submit(self._run, job_id)
        except Exception:
            # A shut-down executor refuses work; without this the job and its key stay stuck.
            # Whoever still holds the fn owns the job's end; if it is gone, `cancel_queued` or a
            # worker already finished (or is running) the job, and finishing it again would
            # double-notify or publish a failure over a live run.
            with self._lock:
                owns = self._fns.pop(job_id, None) is not None
            if not owns:
                return
            log.exception("job_dispatch_failed", job_id=job_id)
            self._finish(job_id, {"state": "failed", "error": _INTERNAL})

    def _run(self, job_id: str) -> None:
        with self._lock:
            fn = self._fns.pop(job_id, None)
            if fn is None:
                # Cancelled while it waited in the executor's queue.
                return
            snapshot = self._jobs[job_id]
            kind = snapshot.kind
            # Claiming the fn and marking it running is one step, so a cancel cannot slip between.
            self._jobs[job_id] = replace(snapshot, state="running", version=snapshot.version + 1)
            self._changed.notify_all()
        with bound_contextvars(job_id=job_id, job_kind=kind):
            # Pre-set so that a BaseException still publishes an end for `wait` and `watch`.
            outcome: dict[str, Any] = {"state": "failed", "error": _INTERNAL}
            try:
                result = fn(self._progress(job_id))
                outcome = {"state": "done", "result": dict(result) if result is not None else None}
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
            except BaseException as exc:
                log.error("job_failed", error=type(exc).__name__)
                raise
            finally:
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
                if queue and not self._closing:
                    next_id = queue.popleft()
                else:
                    self._waiting.pop(final.key, None)
                    self._busy.discard(final.key)
            self._changed.notify_all()
        # Handing over instead of blocking keeps a worker from idling for its key's turn.
        if next_id is not None:
            self._dispatch(next_id)
