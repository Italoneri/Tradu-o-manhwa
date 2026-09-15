"""Um processamento por vez, com progresso que o painel consegue perguntar.

O motor `free` carrega um modelo Argos na memoria e o `claude` consome cota. Dois
jobs ao mesmo tempo nao entregam nada mais rapido e podem estourar a memoria do
WSL, entao o registro aceita um job ativo e responde conflito no segundo.

O historico morre com o processo, e isso esta certo: o resultado que interessa ja
esta em `output/`, e um job terminado so serve para a tela mostrar o que acabou de
acontecer.

Cancelamento fica de fora. Nao ha ponto de interrupcao seguro no meio de um OCR, e
matar a thread deixaria o `extract.json` escrito pela metade - que e pior que
esperar, porque parece completo na proxima execucao.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from .config import Config
from .engines.base import create_engine
from .models import Progress
from .pipeline import extract_chapter, translate_chapter
from .store import build_library, save_library

log = logging.getLogger("mangatl.jobs")

LOG_LINES = 200
"""Ultimas linhas guardadas por job. O suficiente para ver onde parou sem virar
um segundo arquivo de log na memoria."""

HISTORY = 20
"""Jobs lembrados. Acima disso e historico, e historico mora em `output/`."""

JobState = Literal["running", "done", "failed", "cancelled"]
"""`cancelled` nao e produzido por ninguem hoje: cancelar esta fora de escopo, e o
valor existe para o dia em que entrar sem que o contrato mude."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    series: str
    chapter: str
    engine: str
    state: JobState = "running"
    progress: Progress = field(default_factory=lambda: Progress(phase="extract"))
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    error: str | None = None

    def snapshot(self) -> dict:
        """Copia serializavel do estado.

        Copia porque a thread do job continua escrevendo enquanto o handler
        serializa, e `list(deque)` e o unico jeito barato de nao iterar sobre algo
        que cresce debaixo do laco.
        """
        return {
            "id": self.id,
            "series": self.series,
            "chapter": self.chapter,
            "engine": self.engine,
            "state": self.state,
            "progress": self.progress.model_dump(mode="json"),
            "log": list(self.log),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class Busy(RuntimeError):
    """Ja ha um job rodando."""


class _Collector(logging.Handler):
    """Manda as linhas dos loggers `mangatl.*` para o job que esta rodando."""

    def __init__(self, job: Job) -> None:
        super().__init__(level=logging.INFO)
        self._job = job

    def emit(self, record: logging.LogRecord) -> None:
        self._job.log.append(f"{record.levelname} {record.name} {record.getMessage()}")


@contextmanager
def _collecting_into(job: Job) -> Iterator[None]:
    """Anexa o coletor enquanto o job roda, e o solta doa o que doer.

    Removido no `finally` porque handler esquecido no logger faz o job seguinte
    escrever tambem no anterior - e a tela mostraria linha de um job no outro.
    """
    handler = _Collector(job)
    logger = logging.getLogger("mangatl")
    # O nivel tambem sobe: sem `--verbose` ninguem configurou o logger, ele herda o
    # WARNING do root e a linha de INFO morre antes de chegar em handler nenhum.
    # O painel nao tem uma flag de verbose para oferecer - quem abre a tela quer
    # ver o que esta acontecendo.
    before = logger.level
    logger.setLevel(min(logging.INFO, before or logging.INFO))
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(before)


class JobRegistry:
    """Os jobs deste processo. Um ativo, os ultimos vinte lembrados."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: deque[Job] = deque(maxlen=HISTORY)

    def running(self) -> Job | None:
        with self._lock:
            return next((job for job in self._jobs if job.state == "running"), None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return next((job for job in self._jobs if job.id == job_id), None)

    def recent(self) -> list[Job]:
        with self._lock:
            return list(reversed(self._jobs))

    def start(
        self,
        cfg: Config,
        *,
        series: str,
        chapter: str,
        engine: str,
        force: bool = False,
        dry_run: bool = False,
    ) -> Job:
        """Poe um job para rodar numa thread, ou levanta `Busy`.

        A checagem e a insercao acontecem sob o mesmo lock: separadas, dois pedidos
        simultaneos passariam os dois pela checagem antes de qualquer um inserir.
        """
        job = Job(id=uuid.uuid4().hex[:12], series=series, chapter=chapter, engine=engine)
        with self._lock:
            if any(existing.state == "running" for existing in self._jobs):
                raise Busy("ja ha um processamento rodando; deixe terminar")
            self._jobs.append(job)

        thread = threading.Thread(
            target=self._run,
            args=(job, cfg),
            kwargs={"force": force, "dry_run": dry_run},
            name=f"mangatl-job-{job.id}",
            daemon=True,
        )
        thread.start()
        return job

    def _run(self, job: Job, cfg: Config, *, force: bool, dry_run: bool) -> None:
        """O corpo do job. Roda na thread e so escreve no proprio `Job`."""
        outcome: JobState = "done"
        try:
            with _collecting_into(job):
                self._process(job, cfg, force=force, dry_run=dry_run)
        except Exception as error:  # noqa: BLE001 - a falha e o resultado do job
            outcome = "failed"
            job.error = f"{type(error).__name__}: {error}"
            log.exception("operation=job id=%s falhou", job.id)
        finally:
            # `state` por ultimo: quem consulta usa ele para saber que o job
            # acabou, e ver "failed" com `finished_at` ainda em None seria ler o
            # job no meio da escrita.
            job.finished_at = _now()
            job.state = outcome

    def _process(self, job: Job, cfg: Config, *, force: bool, dry_run: bool) -> None:
        def advance(update: Progress) -> None:
            job.progress = update

        report = extract_chapter(cfg, job.series, job.chapter, force=force, progress=advance)
        if not dry_run:
            engine = create_engine(job.engine, cfg)
            translate_chapter(cfg, report.extraction, engine, advance)

        # Sem isto o capitulo novo nao aparece para o leitor, que le o
        # `library.json` salvo e nao o disco. E o mesmo passo final do `process`.
        advance(Progress(phase="library", detail="atualizando o indice"))
        save_library(cfg, build_library(cfg))
        advance(Progress(phase="library", done=1, total=1, detail="pronto"))
