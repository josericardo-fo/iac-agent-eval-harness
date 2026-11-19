"""Ponto de entrada na toolchain: `python3 -m aws_sweep inventory | sweep <baseline.json>`."""

import sys

from . import main

sys.exit(main(sys.argv[1:]))
