"""Turning a selection of stages into a script for a scheduler (or plain shell).

`generate` groups the selected stages into `Job`s and hands them to a
`Template`. A template is anything with `render(jobs) -> str`, so supporting a
new scheduler or cluster setup means writing one small class, or just a
template file (see `FileTemplate`).
"""

from __future__ import annotations

import dataclasses
import importlib
import re
import shlex
import string
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .dag import topo_levels
from .plan import Stage


@dataclasses.dataclass
class Job:
    """One unit of scheduled work: one or more stages run by a single command."""

    id: str  # shell-safe variable name, e.g. JOB_3
    name: str
    stages: list[str]
    deps: list[str]  # ids of the jobs this one must wait for
    command: str  # re-invokes the experiment to run exactly `stages`


class Template(Protocol):
    def render(self, jobs: list[Job]) -> str: ...


def build_jobs(
    stages: list[Stage],
    selected: set[str],
    command: Callable[[list[str]], str],
    bundle: int = 1,
) -> list[Job]:
    """Makes jobs from the selected stages, in dependency order.

    `bundle` packs up to that many independent stages (same topological level)
    into one job. `command` builds the shell command that runs a list of stage
    names.
    """
    if bundle < 1:
        raise ValueError(f"bundle must be >= 1, got {bundle}")
    # Deps outside the selection (already done, or deliberately skipped) don't
    # constrain job order, so only keep edges within it.
    chosen = [
        dataclasses.replace(s, deps=[d for d in s.deps if d in selected])
        for s in stages
        if s.name in selected
    ]
    job_of: dict[str, str] = {}
    jobs: list[Job] = []
    for level in topo_levels(chosen):
        for start in range(0, len(level), bundle):
            batch = level[start : start + bundle]
            names = [s.name for s in batch]
            dep_ids = sorted(
                {job_of[d] for s in batch for d in s.deps if d in job_of},
                key=lambda j: int(j.split("_")[1]),
            )
            job_id = f"JOB_{len(jobs)}"
            for n in names:
                job_of[n] = job_id
            label = names[0] if len(names) == 1 else f"{names[0]}+{len(names) - 1}"
            jobs.append(Job(job_id, label, names, dep_ids, command(names)))
    return jobs


class ShellTemplate:
    """Runs every job in order, one after another. Good for tmux/nohup."""

    def render(self, jobs: list[Job]) -> str:
        lines = ["#!/bin/bash", "set -e", ""]
        for job in jobs:
            lines += [f"# {job.name}", job.command, ""]
        return "\n".join(lines)


class SlurmTemplate:
    """Submits every job with sbatch, wiring --dependency=afterok between them.

    `sbatch_args` is passed to every sbatch call (partition, time, ...);
    `preamble` is emitted once at the top (e.g. `module load ...`).
    """

    def __init__(self, sbatch_args: str = "", preamble: str = ""):
        self.sbatch_args = sbatch_args
        self.preamble = preamble

    def render(self, jobs: list[Job]) -> str:
        lines = ["#!/bin/bash", "set -euo pipefail", ""]
        if self.preamble:
            lines += [self.preamble, ""]
        for job in jobs:
            parts = ["sbatch --parsable", f"--job-name={shlex.quote(job.name)}"]
            if job.deps:
                parts.append("--dependency=afterok:" + ":".join(f"${d}" for d in job.deps))
            if self.sbatch_args:
                parts.append(self.sbatch_args)
            parts.append(f"--wrap={shlex.quote(job.command)}")
            lines.append(f"{job.id}=$({' '.join(parts)} | cut -d';' -f1)")
        return "\n".join(lines) + "\n"


class FileTemplate:
    """A user-owned template file, for schedulers/clusters that need their own shape.

    The file is split at a line containing only `%%job`. The part before it
    (the header) is emitted once; the part after is emitted once per job. Both
    use `string.Template` syntax (`${name}`; write `$$` for a literal `$`).

    Header variables: ${n_jobs}.
    Per-job variables:
      ${id}              shell variable name for this job, e.g. JOB_3
      ${name}            human-readable job name
      ${command}         the command that runs this job's stages (raw)
      ${command_quoted}  the same, shell-quoted for use as one argument
      ${dependency_ids}  ':'-joined `$JOB_x` refs of upstream jobs ('' if none)
      ${dependency_flag} '--dependency=afterok:...' or '' if none
    """

    SEPARATOR = "%%job"

    def __init__(self, path: str | Path):
        text = Path(path).read_text()
        parts = re.split(rf"(?m)^{re.escape(self.SEPARATOR)}[ \t]*$\n?", text, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"Template {path} needs a line containing only '{self.SEPARATOR}'.")
        self.header, self.body = parts

    def render(self, jobs: list[Job]) -> str:
        out = [string.Template(self.header).safe_substitute(n_jobs=len(jobs))]
        for job in jobs:
            dep_ids = ":".join(f"${d}" for d in job.deps)
            out.append(
                string.Template(self.body).safe_substitute(
                    id=job.id,
                    name=job.name,
                    command=job.command,
                    command_quoted=shlex.quote(job.command),
                    dependency_ids=dep_ids,
                    dependency_flag=f"--dependency=afterok:{dep_ids}" if dep_ids else "",
                )
            )
        return "".join(out)


def load_template(spec: str) -> Template:
    """Resolves a CLI `--template` value: 'slurm', 'shell', a file path, or 'pkg.mod:obj'.

    For 'pkg.mod:obj', obj may be a Template instance or a zero-arg factory/class.
    """
    if spec == "shell":
        return ShellTemplate()
    if spec == "slurm":
        return SlurmTemplate()
    if Path(spec).is_file():
        return FileTemplate(spec)
    if ":" in spec:
        module, _, attr = spec.partition(":")
        obj = getattr(importlib.import_module(module), attr)
        return obj if hasattr(obj, "render") and not isinstance(obj, type) else obj()
    raise ValueError(
        f"Unknown template '{spec}': expected 'slurm', 'shell', a template file, or 'module:object'."
    )
