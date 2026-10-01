"""Tipos de dados compartilhados. Sem dependência de I/O nem de SDKs."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    # A ordem de declaração é a ordem da cadeia de avaliação comum.
    VALIDATE = "VALIDATE"
    PLAN = "PLAN"
    OPA = "OPA"
    SANDBOX = "SANDBOX"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNSUPPORTED = "UNSUPPORTED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class FinalStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SANDBOX_UNSUPPORTED = "SANDBOX_UNSUPPORTED"
    HARNESS_INVALID = "HARNESS_INVALID"
    HARNESS_ERROR = "HARNESS_ERROR"  # falha de infraestrutura na avaliação final (não é do agente)


class FirstFailure(StrEnum):
    """Primeira falha da cadeia (vocabulário único para tentativas e avaliação final)."""

    PASS = "PASS"
    VALIDATE_FAIL = "VALIDATE_FAIL"
    PLAN_FAIL = "PLAN_FAIL"
    OPA_FAIL = "OPA_FAIL"
    SANDBOX_FAIL = "SANDBOX_FAIL"
    SANDBOX_UNSUPPORTED = "SANDBOX_UNSUPPORTED"
    HARNESS_ERROR = "HARNESS_ERROR"


class Termination(StrEnum):
    PASSED = "PASSED"
    NO_FEEDBACK = "NO_FEEDBACK"
    SANDBOX_UNSUPPORTED = "SANDBOX_UNSUPPORTED"
    GATE_ERROR = "GATE_ERROR"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


@dataclass(frozen=True)
class Task:
    """Visão da tarefa que o agente pode ver (sem referência nem política)."""

    task_id: str
    prompt: str
    difficulty: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskOracle:
    """Oráculo da tarefa: nunca é entregue ao agente."""

    reference_files: dict[str, str]
    rego_policy: str


@dataclass(frozen=True)
class Artifact:
    files: dict[str, str]

    @property
    def sha256(self) -> str:
        # Determinístico: independe da ordem de inserção dos arquivos.
        h = hashlib.sha256()
        for name in sorted(self.files):
            for part in (name, self.files[name]):
                data = part.encode()
                h.update(len(data).to_bytes(8, "big"))
                h.update(data)
        return h.hexdigest()


@dataclass(frozen=True)
class CheckResult:
    stage: Stage
    status: CheckStatus
    message: str = ""
    raw: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class AgentReply:
    artifact: Artifact
    usage: Usage
    model_id: str
    request_id: str | None = None
    raw_text: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Feedback:
    stage: Stage
    error_class: str
    message: str


@dataclass(frozen=True)
class Turn:
    """Uma rodada do histórico: o artefato gerado e o feedback recebido sobre ele."""

    artifact: Artifact
    feedback: Feedback


@dataclass
class AttemptRecord:
    run_id: str
    task_id: str
    condition: str
    rep: int
    difficulty: int
    dataset_version: str
    model_provider: str
    model_id: str
    params: dict[str, Any]
    request_id: str | None
    attempt_index: int
    shared: bool
    statuses: dict[str, str]
    first_failure_stage: str
    first_failure_status: str | None
    diagnostic: str
    diagnostic_chars: int
    error_class: str | None
    termination_reason: str | None
    feedback_sent: str | None
    durations: dict[str, float]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    artifact_path: str
    artifact_hash: str
    response_path: str
    event: str = "attempt"

    def to_event(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinalRecord:
    run_id: str
    task_id: str
    condition: str
    rep: int
    difficulty: int
    dataset_version: str
    model_provider: str
    model_id: str
    params: dict[str, Any]
    attempts: int
    termination_reason: str | None
    statuses: dict[str, str]
    first_failure_stage: str
    diagnostic: str
    error_class: str | None
    # Tentativa 1 (compartilhada entre as condições) separada dos reparos da condição.
    shared_input_tokens: int
    shared_output_tokens: int
    repair_input_tokens: int
    repair_output_tokens: int
    artifact_path: str
    artifact_hash: str
    final_status: str
    sandbox_supported: bool
    event: str = "final"

    def to_event(self) -> dict[str, Any]:
        return asdict(self)
