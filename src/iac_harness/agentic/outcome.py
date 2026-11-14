"""Regra única de sucesso de uma trajetória do agente (E3).

O colateral (`collateral_ok`) é reportado à parte e não entra aqui.

Com usuário simulado há duas regras independentes:
- `success` (turno 1): `expect` vale só para o turno 1, como sempre (P6/E2 inalterados). Em
  `expect = "ask"`, o `check.py` confere o estado intocado e o agente precisa ter perguntado.
- `success_final`: o estado depois das respostas passa no check final, tenha o agente perguntado
  ou não. O check final é o `check_final.py` da tarefa, se existir, senão o `check.py`
  (`check_pass_final`). Se o agente perguntou ou não fica em `asked`, à parte.
"""

from __future__ import annotations


def success(expect: str, check_pass: bool | None, asked: bool | None) -> bool | None:
    """`act`: check passou e o agente não perguntou. `ask`: perguntou e o estado ficou intocado.

    Nas tarefas `expect = "ask"` o check confere o estado intocado (gate invertido), então
    "intocado" é `check_pass`. None enquanto o juiz não classificou (`asked` None) ou se o check
    não produziu resultado.
    """
    if expect not in ("act", "ask"):
        raise ValueError(f"expect inválido: {expect!r}")
    if asked is None or check_pass is None:
        return None
    if expect == "act":
        return check_pass and not asked
    return check_pass and asked


def success_final(check_pass_final: bool | None) -> bool | None:
    """Com usuário simulado: o estado final (depois das respostas) passa no check final.

    Diferente de `success` (turno 1), perguntar não reprova nem aprova aqui: vale o que o agente
    entrega depois que o usuário respondeu. `check_pass_final` já vem do `check_final.py` (ou do
    `check.py`, sem ele). None se o check não produziu resultado.
    """
    return check_pass_final
