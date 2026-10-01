"""Condições experimentais como dado: o que muda é só o feedback disponível no loop."""

from __future__ import annotations

from dataclasses import dataclass

from iac_harness.types import Stage


@dataclass(frozen=True)
class Condition:
    name: str
    loop_stages: tuple[Stage, ...]
    max_attempts: int


_PRE = (Stage.VALIDATE, Stage.PLAN, Stage.OPA)

A = Condition("A", (), 1)
B = Condition("B", _PRE, 3)
C = Condition("C", (*_PRE, Stage.SANDBOX), 3)
