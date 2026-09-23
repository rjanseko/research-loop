from __future__ import annotations

import argparse
from pathlib import Path

from .graph import render_research_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the research graph as Mermaid")
    parser.add_argument("--direction", choices=["LR", "RL", "TB", "BT"], default="LR")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    diagram = render_research_graph(direction=args.direction)
    if args.output:
        args.output.write_text(diagram, encoding="utf-8")
    else:
        print(diagram)


if __name__ == "__main__":
    main()
