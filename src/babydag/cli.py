"""Command line for an experiment class: `babydag.main(MyExperiment)`.

    python my_experiment.py status 0
    python my_experiment.py run 0 [--only REGEX] [--rerun] [--no-deps] [--fresh]
    python my_experiment.py generate 0 --template slurm [--bundle N]

An experiment is a plain class with a `build(self, plan)` method. Its public,
non-callable class attributes are its params, settable as `--name value`.
Optionally define `experiment_dir(self, seed) -> str` (relative to
$BABYDAG_ROOT); the default is `<Class>/<Class>_<seed>`. `generate` defaults can
also be set per experiment: `template(self)` returns a Template object or a
`--template` spec string (default: shell), and `python(self)` returns the command
jobs use to run python, e.g. "uv run python" (default: python).
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import dag, templates
from .plan import Plan, root_dir


def param_names(cls: type) -> list[str]:
    return [k for k in vars(cls) if not k.startswith("_") and not callable(getattr(cls, k))]


def experiment_dir(inst: Any, seed: int) -> str:
    if hasattr(inst, "experiment_dir"):
        return inst.experiment_dir(seed)
    name = type(inst).__name__
    return f"{name}/{name}_{seed}"


def build_plan(cls: type, params: dict[str, Any] | None = None, seed: int = 0) -> Plan:
    """Instantiates the experiment with `params` and builds its Plan."""
    params = params or {}
    valid = param_names(cls)
    for key in params:
        if key not in valid:
            raise ValueError(
                f"Unknown param '{key}' for experiment '{cls.__name__}'. Valid params: {sorted(valid)}"
            )
    inst = cls()
    for key, value in params.items():
        setattr(inst, key, value)
    plan = Plan(root_dir() / experiment_dir(inst, seed), seed)
    inst.build(plan)
    return plan


def _parse_bool(text: str) -> bool:
    if text.lower() in ("1", "true", "yes", "y"):
        return True
    if text.lower() in ("0", "false", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {text!r}")


def _type_for(default: Any):
    if isinstance(default, bool):
        return _parse_bool
    if isinstance(default, (list, tuple)):
        elem = type(default[0]) if default else str
        elem = _parse_bool if elem is bool else elem
        return lambda text: [elem(x) for x in text.split(",") if x != ""]
    if default is None:
        return str
    return type(default)


def _flags(params: dict[str, Any]) -> str:
    def fmt(v: Any) -> str:
        return ",".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)

    return "".join(f" --{k}={shlex.quote(fmt(v))}" for k, v in params.items())


def _script_target(cls: type) -> str:
    """How a cluster job should invoke this experiment: `-m pkg.mod` or the script path."""
    module = cls.__module__
    if module == "__main__":
        spec = getattr(sys.modules.get("__main__"), "__spec__", None)
        if spec is not None and spec.name:
            return f"-m {spec.name}"
        return shlex.quote(str(Path(sys.argv[0]).resolve()))
    return f"-m {module}"


def main(cls: type, argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for an experiment module: `babydag.main(MyExperiment)`."""
    defaults = {k: getattr(cls, k) for k in param_names(cls)}

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("seed", type=int, help="pipeline seed")
    common.add_argument("--only", metavar="REGEX", help="restrict to stages whose name matches")
    common.add_argument("--rerun", action="store_true", help="also select finished stages")
    common.add_argument("--no-deps", action="store_true", help="don't pull in unfinished upstream stages")
    group = common.add_argument_group("experiment params")
    for key, default in defaults.items():
        group.add_argument(
            f"--{key}", dest=f"param_{key}", type=_type_for(default), default=argparse.SUPPRESS,
            help=f"(default: {default!r})",
        )

    parser = argparse.ArgumentParser(prog=cls.__name__, description=cls.__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", parents=[common], help="print the DAG and what's done")
    run = sub.add_parser("run", parents=[common], help="run selected stages locally")
    run.add_argument("--fresh", action="store_true", help="wipe the experiment dir first")
    run.add_argument("--dry-run", action="store_true", help="show the plan, run nothing")
    run.add_argument("--show", action="store_true", help="print each stage's script, run nothing")
    gen = sub.add_parser("generate", parents=[common], help="print a scheduler script")
    gen.add_argument("--template", help="slurm | shell | FILE | module:object (default: the experiment's template(), else shell)")
    gen.add_argument("--bundle", type=int, default=1, help="max independent stages per job")
    gen.add_argument("--python", help="command jobs use to run python, e.g. 'uv run python' (default: the experiment's python(), else python)")

    args = parser.parse_args(argv)
    params = {
        k[len("param_"):]: v for k, v in vars(args).items() if k.startswith("param_")
    }
    if args.command == "run" and args.fresh:
        inst_dir = root_dir() / experiment_dir(_instance(cls, params), args.seed)
        if args.dry_run:
            print(f"--fresh --dry-run: would remove {inst_dir}")
        elif inst_dir.exists():
            print(f"--fresh: removing {inst_dir}")
            shutil.rmtree(inst_dir)

    plan = build_plan(cls, params, args.seed)
    chosen = dag.select(
        plan.stages, args.only, include_finished=args.rerun, with_deps=not args.no_deps
    )

    if args.command == "status":
        dag.print_status(plan.stages, chosen, title=plan.name)
    elif args.command == "run":
        dag.print_status(plan.stages, chosen, title=plan.name)
        if not chosen:
            print("Nothing to run.")
            return
        dag.run_local(plan.stages, chosen, dry_run=args.dry_run, show=args.show)
    else:
        inst = _instance(cls, params)
        template = args.template or (inst.template() if hasattr(inst, "template") else "shell")
        if isinstance(template, str):
            template = templates.load_template(template)
        python = args.python or (inst.python() if hasattr(inst, "python") else "python")

        def command(names: list[str]) -> str:
            pattern = "^(" + "|".join(re.escape(n) for n in names) + ")$"
            return (
                f"{python} {_script_target(cls)} run {args.seed}{_flags(params)}"
                f" --no-deps --only {shlex.quote(pattern)}"
            )

        jobs = templates.build_jobs(plan.stages, chosen, command, args.bundle)
        if not jobs:
            print("Nothing to generate: no stages selected.", file=sys.stderr)
            return
        print(template.render(jobs))


def _instance(cls: type, params: dict[str, Any]) -> Any:
    inst = cls()
    for key, value in params.items():
        setattr(inst, key, value)
    return inst
