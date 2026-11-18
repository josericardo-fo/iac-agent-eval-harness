"""demo-timeline: trajetória gravada -> timeline.json para o vídeo da demo."""

from __future__ import annotations

import argparse
import sys

from iac_harness.demo.timeline import build_timeline, dump_timeline


def cmd_demo_timeline(args: argparse.Namespace) -> int:
    try:
        timeline = build_timeline(args.unit_dir, args.scene)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"erro: {exc}") from exc
    text = dump_timeline(timeline) + "\n"
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0
