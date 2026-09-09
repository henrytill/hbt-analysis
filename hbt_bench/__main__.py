"""Entry point."""

from __future__ import annotations

import subprocess
import sys

from hbt_bench import cli, core


def main() -> int:
    """Turn the expected failures into a message and an exit status."""
    try:
        return cli.run(cli.parse_args())
    except core.BenchmarkError as exc:
        print(f"hbt-bench: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"hbt-bench: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
