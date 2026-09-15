from __future__ import annotations

import logging
import threading
import time

import pytest

from .config import Config
from .jobs import HISTORY, LOG_LINES, Busy, JobRegistry
from .models import Progress


class FakeRegistry(JobRegistry):
    """Registro que nao dispara pipeline nenhum.

    O job fica preso ate o teste liberar, que e o unico jeito de observar o estado
    `running` sem depender de quanto tempo um OCR de verdade demora.
    """

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.blow_up = False

    def _process(self, job, cfg, *, force: bool, dry_run: bool) -> None:  # noqa: ANN001
        job.progress = Progress(phase="extract", done=1, total=2, detail="fingindo")
        logging.getLogger("mangatl.teste").info("linha que o job coletou")
        self.release.wait(timeout=5)
        if self.blow_up:
            raise RuntimeError("estourou de proposito")


@pytest.fixture
def cfg(tmp_path) -> Config:  # noqa: ANN001
    return Config(root=tmp_path)


def wait_until(condition, timeout: float = 5.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def start(registry: JobRegistry, cfg: Config, chapter: str = "001"):  # noqa: ANN201
    return registry.start(cfg, series="serie", chapter=chapter, engine="free")


def test_reports_the_job_as_running_until_it_finishes(cfg: Config):
    registry = FakeRegistry()
    job = start(registry, cfg)

    assert wait_until(lambda: registry.running() is not None)
    assert job.state == "running"
    assert job.finished_at is None

    registry.release.set()
    assert wait_until(lambda: job.state == "done")
    assert job.finished_at is not None
    assert registry.running() is None


def test_refuses_a_second_job_while_one_runs(cfg: Config):
    # Dois jobs nao entregam nada mais rapido: o motor free carrega um modelo na
    # memoria e o claude consome cota.
    registry = FakeRegistry()
    start(registry, cfg)
    wait_until(lambda: registry.running() is not None)

    with pytest.raises(Busy, match="ja ha um processamento"):
        start(registry, cfg, "002")

    registry.release.set()


def test_accepts_the_next_job_after_the_first_ends(cfg: Config):
    registry = FakeRegistry()
    first = start(registry, cfg)
    registry.release.set()
    wait_until(lambda: first.state == "done")

    registry.release.clear()
    second = start(registry, cfg, "002")
    registry.release.set()

    assert wait_until(lambda: second.state == "done")
    assert first.id != second.id


def test_keeps_the_failure_as_the_result_of_the_job(cfg: Config):
    registry = FakeRegistry()
    registry.blow_up = True
    job = start(registry, cfg)
    registry.release.set()

    assert wait_until(lambda: job.state == "failed")
    assert job.error is not None
    assert "estourou de proposito" in job.error
    assert job.finished_at is not None


def test_collects_the_log_lines_of_the_running_job(cfg: Config):
    registry = FakeRegistry()
    job = start(registry, cfg)
    registry.release.set()
    wait_until(lambda: job.state == "done")

    assert any("linha que o job coletou" in line for line in job.log)


def test_stops_collecting_when_the_job_ends(cfg: Config):
    # Handler esquecido no logger faria o job seguinte escrever tambem no anterior.
    registry = FakeRegistry()
    job = start(registry, cfg)
    registry.release.set()
    wait_until(lambda: job.state == "done")
    before = len(job.log)

    logging.getLogger("mangatl.teste").info("depois que acabou")

    assert len(job.log) == before


def test_keeps_only_the_last_lines(cfg: Config):
    registry = FakeRegistry()
    job = start(registry, cfg)
    for index in range(LOG_LINES + 50):
        job.log.append(str(index))
    registry.release.set()

    assert len(job.log) == LOG_LINES
    assert job.log[-1] == str(LOG_LINES + 49)


def test_remembers_the_most_recent_jobs_newest_first(cfg: Config):
    registry = FakeRegistry()
    registry.release.set()
    ids = []
    for index in range(3):
        job = start(registry, cfg, f"00{index}")
        wait_until(lambda job=job: job.state == "done")
        ids.append(job.id)

    assert [job.id for job in registry.recent()] == list(reversed(ids))


def test_forgets_the_oldest_job_when_the_history_is_full(cfg: Config):
    registry = FakeRegistry()
    registry.release.set()
    first = start(registry, cfg)
    wait_until(lambda: first.state == "done")
    for index in range(HISTORY):
        job = start(registry, cfg, f"x{index}")
        wait_until(lambda job=job: job.state == "done")

    assert registry.get(first.id) is None
    assert len(registry.recent()) == HISTORY


def test_serializes_without_leaking_the_live_log(cfg: Config):
    registry = FakeRegistry()
    job = start(registry, cfg)
    registry.release.set()
    wait_until(lambda: job.state == "done")

    snapshot = job.snapshot()
    job.log.append("depois do retrato")

    assert isinstance(snapshot["log"], list)
    assert "depois do retrato" not in snapshot["log"]
    assert snapshot["progress"]["phase"] == "extract"
    assert set(snapshot) == {
        "id",
        "series",
        "chapter",
        "engine",
        "state",
        "progress",
        "log",
        "started_at",
        "finished_at",
        "error",
    }
