"""Execute a configured product's shared authoring CLI with an argv list."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.local.json")
    parser.add_argument("--product", choices=("sonic", "score"), required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        configuration = json.loads(args.config.read_text(encoding="utf-8"))[args.product]
        executable, root = Path(configuration["python"]), Path(configuration["root"])
        workspace = args.workspace or Path(configuration["workspace"])
        if not executable.is_absolute() or not executable.is_file() or not root.is_absolute() or not root.is_dir():
            raise ValueError("Configuration must name an existing absolute interpreter and product checkout")
        if not workspace.is_absolute():
            raise ValueError("Workspace must be an explicit absolute path")
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            command = ["capabilities", "--json"]
        module = ["score_matter", "audio"] if args.product == "score" else ["tools.authoring"]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        environment.pop("PYTHONPATH", None)
        completed = subprocess.run([str(executable), "-m", *module, "--workspace", str(workspace), *command],
                                   cwd=root, env=environment, timeout=args.timeout)
        return completed.returncode
    except subprocess.TimeoutExpired:
        error = {"code": "host_timeout", "message": "Execution timed out; query the same request ID before another action."}
    except (OSError, ValueError, KeyError) as exc:
        error = {"code": "host_configuration_error", "message": str(exc)}
    print(json.dumps({"schema": "matter-error/v1", "status": "failed", "error": error}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
