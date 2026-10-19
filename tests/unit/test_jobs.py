from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from structlog.testing import capture_logs

from dental_image_organizer.errors import CaseNotFoundError, JobNotFoundError
from dental_image_organizer.services.jobs import JobError, JobManager, JobProgress, JobSnapshot

TIMEOUT = 5.0


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    pool = ThreadPoolExecutor(max_workers=2)
    yield pool
    pool.shutdown(wait=True)


@pytest.fixture
def jobs(executor: ThreadPoolExecutor) -> JobManager:
    return JobManager(executor)


def test_a_job_reports_progress_and_its_result(jobs: JobManager) -> None:
    def fn(progress: JobProgress) -> dict[str, int]:
        progress(1, 2)
        progress(2, 2, "two")
        return {"n": 2}

    snap = jobs.wait(jobs.submit("demo", fn, total=2).id, TIMEOUT)

    assert snap.state == "done"
    assert snap.done == snap.total == 2
    assert snap.message == "two"
    assert snap.result == {"n": 2}


def test_domain_errors_keep_their_code(jobs: JobManager) -> None:
    def fn(progress: JobProgress) -> None:
        raise CaseNotFoundError("gone")

    snap = jobs.wait(jobs.submit("demo", fn).id, TIMEOUT)

    assert snap.state == "failed"
    assert snap.error == JobError("case_not_found", "gone")


def test_unexpected_errors_are_logged_but_not_shown(jobs: JobManager) -> None:
    def fn(progress: JobProgress) -> None:
        raise RuntimeError("Alex Wang")

    with capture_logs() as logs:
        snap = jobs.wait(jobs.submit("demo", fn).id, TIMEOUT)

    assert snap.error == JobError(
        "internal_error", "An unexpected error occurred; see the log file."
    )
    assert any(e["event"] == "job_failed" for e in logs)


def test_os_errors_never_carry_the_path(jobs: JobManager) -> None:
    def fn(progress: JobProgress) -> None:
        raise FileNotFoundError(2, "No such file", "/lib/Alex Wang/x.jpg")

    def storage_fn(progress: JobProgress) -> None:
        from dental_image_organizer.errors import StorageError

        try:
            raise FileNotFoundError(2, "No such file", "/lib/Alex Wang/x.jpg")
        except OSError as exc:
            raise StorageError("Cannot read the case.") from exc

    with capture_logs() as logs:
        snap = jobs.wait(jobs.submit("demo", fn).id, TIMEOUT)
        chained = jobs.wait(jobs.submit("demo", storage_fn).id, TIMEOUT)

    assert snap.error == JobError("io_error", "No such file")
    assert chained.error is not None
    assert chained.error.code == "storage_error"
    assert {e["event"] for e in logs} == {"job_failed"}
    assert all("Alex Wang" not in repr(e) for e in logs)
    assert all("exc_info" not in e and "exception" not in e for e in logs)


def test_jobs_of_one_key_run_in_order(jobs: JobManager) -> None:
    release = threading.Event()
    started = threading.Event()
    order: list[str] = []

    def first(progress: JobProgress) -> None:
        started.set()
        assert release.wait(TIMEOUT)
        order.append("a-end")

    def second(progress: JobProgress) -> None:
        order.append("b-start")

    a = jobs.submit("demo", first, key="c1")
    assert started.wait(TIMEOUT)
    b = jobs.submit("demo", second, key="c1")

    assert jobs.get(b.id).state == "queued"
    release.set()
    assert jobs.wait(a.id, TIMEOUT).state == "done"
    assert jobs.wait(b.id, TIMEOUT).state == "done"
    assert order == ["a-end", "b-start"]


def test_a_waiting_job_does_not_hold_a_worker(jobs: JobManager) -> None:
    # Two queued jobs of one key must not occupy both workers of the pool.
    release = threading.Event()
    started = threading.Event()

    def blocked(progress: JobProgress) -> None:
        started.set()
        assert release.wait(TIMEOUT)

    a = jobs.submit("demo", blocked, key="c1")
    assert started.wait(TIMEOUT)
    b = jobs.submit("demo", lambda p: None, key="c1")
    c = jobs.submit("demo", lambda p: {"ok": True}, key="c2")

    assert jobs.wait(c.id, TIMEOUT).state == "done"
    assert jobs.get(b.id).state == "queued"
    release.set()
    assert jobs.wait(b.id, TIMEOUT).state == "done"
    assert jobs.wait(a.id, TIMEOUT).state == "done"


def test_jobs_of_different_keys_run_together(jobs: JobManager) -> None:
    barrier = threading.Barrier(2, timeout=TIMEOUT)

    def fn(progress: JobProgress) -> None:
        barrier.wait()

    a = jobs.submit("demo", fn, key="c1")
    b = jobs.submit("demo", fn, key="c2")

    assert jobs.wait(a.id, TIMEOUT).state == "done"
    assert jobs.wait(b.id, TIMEOUT).state == "done"


def test_active_reports_queued_and_running_jobs(jobs: JobManager) -> None:
    release = threading.Event()
    started = threading.Event()

    def fn(progress: JobProgress) -> None:
        started.set()
        assert release.wait(TIMEOUT)

    a = jobs.submit("demo", fn, key="c1")
    assert started.wait(TIMEOUT)

    assert jobs.active("c1")
    assert not jobs.active("c2")
    assert jobs.active()
    assert jobs.active_any(["c2", "c1"])
    release.set()
    jobs.wait(a.id, TIMEOUT)
    assert not jobs.active("c1")
    assert not jobs.active_any(["c2", "c1"])


def test_watch_yields_every_state_until_finished(jobs: JobManager) -> None:
    release = threading.Event()

    def fn(progress: JobProgress) -> dict[str, int]:
        assert release.wait(TIMEOUT)
        progress(1, 1)
        return {"n": 1}

    job_id = jobs.submit("demo", fn).id

    async def collect() -> list[JobSnapshot]:
        seen: list[JobSnapshot] = []
        async for snap in jobs.watch(job_id, interval=0.01):
            seen.append(snap)
            if len(seen) == 1:
                release.set()
        return seen

    seen = asyncio.run(asyncio.wait_for(collect(), timeout=10))

    versions = [s.version for s in seen]
    assert versions == sorted(set(versions))
    assert seen[-1].state == "done"


def test_finished_jobs_are_forgotten_beyond_the_limit(executor: ThreadPoolExecutor) -> None:
    jobs = JobManager(executor, keep_finished=2)
    ids = [jobs.wait(jobs.submit("demo", lambda p: None).id, TIMEOUT).id for _ in range(3)]

    with pytest.raises(JobNotFoundError):
        jobs.get(ids[0])
    assert jobs.get(ids[2]).state == "done"


def test_on_finish_never_breaks_a_job(executor: ThreadPoolExecutor) -> None:
    calls: list[str] = []

    def hook(snapshot: JobSnapshot) -> None:
        calls.append(snapshot.id)
        raise RuntimeError("hook failed")

    jobs = JobManager(executor, on_finish=hook)
    first = jobs.submit("demo", lambda p: None)
    second = jobs.submit("demo", lambda p: None)

    assert jobs.wait(first.id, TIMEOUT).state == "done"
    assert jobs.wait(second.id, TIMEOUT).state == "done"
    assert sorted(calls) == sorted([first.id, second.id])


def test_cancel_queued_fails_waiting_jobs(jobs: JobManager) -> None:
    release = threading.Event()
    started = threading.Event()

    def blocked(progress: JobProgress) -> None:
        started.set()
        assert release.wait(TIMEOUT)

    a = jobs.submit("demo", blocked, key="c1")
    assert started.wait(TIMEOUT)
    b = jobs.submit("demo", lambda p: None, key="c1")

    assert jobs.cancel_queued() == 1
    snap = jobs.get(b.id)
    assert snap.state == "failed"
    assert snap.error == JobError("cancelled", "The app was closing.")
    release.set()
    assert jobs.wait(a.id, TIMEOUT).state == "done"


def test_cancel_queued_also_fails_jobs_waiting_in_the_executor() -> None:
    pool = ThreadPoolExecutor(max_workers=2)
    jobs = JobManager(pool)
    release = threading.Barrier(3, timeout=TIMEOUT)
    running = threading.Semaphore(0)
    ran: list[str] = []

    def blocked(progress: JobProgress) -> None:
        running.release()
        release.wait()

    def third(progress: JobProgress) -> None:
        ran.append("c3")

    a = jobs.submit("demo", blocked, key="c1")
    b = jobs.submit("demo", blocked, key="c2")
    assert running.acquire(timeout=TIMEOUT)
    assert running.acquire(timeout=TIMEOUT)
    c = jobs.submit("demo", third, key="c3")

    assert jobs.cancel_queued() == 1
    assert jobs.get(c.id).error == JobError("cancelled", "The app was closing.")
    release.wait()
    pool.shutdown(wait=True)

    assert ran == []
    assert jobs.get(a.id).state == jobs.get(b.id).state == "done"
    assert jobs.get(c.id).state == "failed"


def test_submitting_after_close_fails_the_job_at_once(jobs: JobManager) -> None:
    jobs.cancel_queued()

    snap = jobs.submit("demo", lambda p: None, key="k")

    assert snap.state == "failed"
    assert snap.error == JobError("cancelled", "The app was closing.")
    assert not jobs.active("k")
    assert jobs.wait(snap.id, 0.5).state == "failed"


def test_a_shut_down_executor_fails_the_job_and_frees_its_key() -> None:
    pool = ThreadPoolExecutor(max_workers=1)
    jobs = JobManager(pool)
    pool.shutdown(wait=True)

    snap = jobs.submit("demo", lambda p: None, key="k")

    final = jobs.wait(snap.id, 0.5)
    assert final.state == "failed"
    assert final.error == JobError(
        "internal_error", "An unexpected error occurred; see the log file."
    )
    assert not jobs.active("k")


def test_a_base_exception_still_ends_the_job_and_frees_the_key(
    executor: ThreadPoolExecutor,
) -> None:
    jobs = JobManager(executor)

    def fn(progress: JobProgress) -> None:
        raise KeyboardInterrupt

    first = jobs.submit("demo", fn, key="k")
    snap = jobs.wait(first.id, TIMEOUT)
    second = jobs.submit("demo", lambda p: {"ok": 1}, key="k")

    assert snap.state == "failed"
    assert snap.error is not None and snap.error.code == "internal_error"
    assert jobs.wait(second.id, TIMEOUT).state == "done"


def test_a_failing_on_finish_hook_never_logs_its_path(executor: ThreadPoolExecutor) -> None:
    def hook(snapshot: JobSnapshot) -> None:
        raise FileNotFoundError(2, "No such file", "/lib/Alex Wang/x")

    jobs = JobManager(executor, on_finish=hook)

    with capture_logs() as logs:
        snap = jobs.wait(jobs.submit("demo", lambda p: None).id, TIMEOUT)

    assert snap.state == "done"
    assert logs and all("Alex Wang" not in repr(e) for e in logs)
    assert all("exc_info" not in e for e in logs)


class _ClosingExecutor:
    """Runs jobs by hand; its `submit` can close the manager and then refuse, like a shutdown."""

    def __init__(self) -> None:
        self.manager: JobManager | None = None
        self.close_on_submit = False
        self.pending: list[Callable[[str], None]] = []
        self.args: list[str] = []

    def submit(self, fn: Callable[[str], None], job_id: str) -> None:
        if self.close_on_submit:
            assert self.manager is not None
            self.manager.cancel_queued()
            raise RuntimeError("cannot schedule new futures after shutdown")
        self.pending.append(fn)
        self.args.append(job_id)


@pytest.mark.parametrize("path", ["submit", "handoff"])
def test_a_dispatch_failing_during_close_finishes_the_job_once(path: str) -> None:
    executor = _ClosingExecutor()
    finished: list[str] = []
    jobs = JobManager(executor, on_finish=lambda s: finished.append(s.id))  # type: ignore[arg-type]
    executor.manager = jobs

    with capture_logs() as logs:
        if path == "submit":
            executor.close_on_submit = True
            target = jobs.submit("demo", lambda p: None, key="k")
            last = target.id
        else:
            first = jobs.submit("demo", lambda p: None, key="k")
            last = jobs.submit("demo", lambda p: None, key="k").id
            executor.close_on_submit = True
            executor.pending[0](executor.args[0])
            assert jobs.get(first.id).state == "done"

    snap = jobs.get(last)
    assert snap.state == "failed"
    assert snap.error == JobError("cancelled", "The app was closing.")
    assert sorted(finished) == sorted(set(finished))
    assert finished.count(last) == 1
    assert not any(e["log_level"] in ("error", "critical") for e in logs)
    assert not jobs.active("k")
