"""Regra única de sucesso de uma trajetória do agente (E3).

O colateral (`collateral_ok`) é reportado à parte e não entra aqui.
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
