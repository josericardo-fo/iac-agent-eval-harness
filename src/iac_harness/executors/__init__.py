"""Executores (Floci, LocalStack, AWS real) e a limpeza final comum a todos."""

from __future__ import annotations

import sys
from typing import Any


def final_cleanup(executor: Any) -> None:
    """Limpeza ao fim do run (chamada num `finally`): na AWS real, apaga as sobras do último par.

    Se a limpeza falhar, o erro vai ao stderr; só sobe quando não há exceção original a preservar.
    """
    pending = sys.exc_info()[1] is not None
    try:
        executor.cleanup()
    except Exception as exc:
        print(f"falha na limpeza final ({executor.NAME}): {exc}", file=sys.stderr)
        if not pending:
            raise
