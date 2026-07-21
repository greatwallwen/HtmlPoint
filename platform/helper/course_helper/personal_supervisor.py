"""Bounded asynchronous supervisor for persisted personal course runs."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef
from course_helper.domain.personal_course import PersonalCourseRun
from course_helper.personal_orchestrator import RuntimeConfig, resume_personal_course
from course_helper.personal_runs import list_resumable_personal_runs


ResumeFunction = Callable[[RuntimeConfig, str, ActorRef], PersonalCourseRun]


class PersonalCourseSupervisor:
    """Run at most a small number of resumable personal course state machines."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        max_workers: int = 1,
        resume_fn: ResumeFunction = resume_personal_course,
    ) -> None:
        if type(max_workers) is not int or not 1 <= max_workers <= 4:
            raise ValueError("personal course workers must be from 1 to 4")
        self._config = config
        self._resume_fn = resume_fn
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="personal-course",
        )
        self._active: dict[str, Future[PersonalCourseRun]] = {}
        self._lock = Lock()
        self._shutdown = False

    def start(self, run_id: str, actor: ActorRef) -> Future[PersonalCourseRun]:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("personal course supervisor is shut down")
            existing = self._active.get(run_id)
            if existing is not None and not existing.done():
                return existing
            future = self._executor.submit(
                self._resume_fn,
                self._config,
                run_id,
                actor,
            )
            self._active[run_id] = future
        future.add_done_callback(
            lambda completed, current_run_id=run_id: self._forget(
                current_run_id, completed
            )
        )
        return future

    def _forget(
        self,
        run_id: str,
        completed: Future[PersonalCourseRun],
    ) -> None:
        with self._lock:
            if self._active.get(run_id) is completed:
                self._active.pop(run_id, None)

    def resume_pending(self) -> tuple[Future[PersonalCourseRun], ...]:
        with KnowledgeCatalog.open(self._config.database_path) as catalog:
            runs = list_resumable_personal_runs(catalog)
        actor = ActorRef(actor_type="system", actor_id="personal-resume")
        return tuple(self.start(run.run_id, actor) for run in runs)

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


__all__ = ["PersonalCourseSupervisor"]
