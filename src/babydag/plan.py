"""Plan, Stage and Handle: how an experiment describes its DAG."""

from __future__ import annotations

import dataclasses
import hashlib
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


def root_dir() -> Path:
    """Where experiments live on disk: $BABYDAG_ROOT, default ~/babydag."""
    return Path(os.environ.get("BABYDAG_ROOT", "~/babydag")).expanduser().resolve()


@dataclasses.dataclass
class Handle:
    """A "future": the stage(s) that produce something, plus an optional payload.

    Pass handles in `deps` to make a later stage wait on them. `value` is
    whatever the producer wants downstream code to know up front, typically the
    output directory, so it can be interpolated into later commands.
    """

    names: list[str]
    value: Any = None


@dataclasses.dataclass
class Stage:
    """One node of the DAG: a list of shell commands, or an in-process function."""

    name: str
    deps: list[str]
    marker: Path
    cmds: list[str] | None = None
    fn: Callable[[], None] | None = None
    group: str = ""
    workdir: Path | None = None  # created before cmds run

    def done(self) -> bool:
        return self.marker.exists()

    def script(self) -> str:
        """Bash for this stage: fail fast, run the commands, then touch the marker."""
        if self.cmds is None:
            raise ValueError(f"Stage '{self.name}' runs a python fn, it has no script.")
        lines = ["#!/bin/bash", "set -e"]
        if self.workdir is not None:
            lines.append(f'mkdir -p "{self.workdir}"')
        lines += self.cmds
        lines.append(f'mkdir -p "{self.marker.parent}"')
        lines.append(f'touch "{self.marker}"')
        return "\n".join(lines) + "\n"


def dep_names(deps: Iterable[Handle | str] | Handle | str | None) -> list[str]:
    """Flattens a mix of Handles and stage-name strings into stage names."""
    if deps is None:
        return []
    if isinstance(deps, (Handle, str)):
        deps = [deps]
    names: list[str] = []
    for dep in deps:
        if isinstance(dep, Handle):
            names.extend(dep.names)
        elif isinstance(dep, str):
            names.append(dep)
        else:
            raise TypeError(f"deps entries must be Handle or str, got {type(dep)}")
    return names


@dataclasses.dataclass
class _Shared:
    stages: list[Stage] = dataclasses.field(default_factory=list)
    names: set[str] = dataclasses.field(default_factory=set)


class Plan:
    """The DAG of one experiment run, built up by `add_stage` calls.

    `scope(label)` returns a child plan that shares the same DAG but namespaces
    stage names ("label:stage"), directories and seeds, so a loop over rounds or
    environments can reuse the same stage names without collisions.
    """

    def __init__(
        self,
        exp_dir: str | Path,
        seed: int = 0,
        *,
        _shared: _Shared | None = None,
        _scopes: list[str] | None = None,
    ):
        self.exp_dir = Path(exp_dir)
        self.pipeline_seed = seed
        self._shared = _shared if _shared is not None else _Shared()
        self._scopes = list(_scopes or [])

    @property
    def stages(self) -> list[Stage]:
        return self._shared.stages

    @property
    def name(self) -> str:
        """Run name: exp_dir leaf plus scope labels, e.g. 'Exp_0_round_3'."""
        return "_".join([self.exp_dir.name, *self._scopes])

    @property
    def _prefix(self) -> str:
        return "".join(f"{label}:" for label in self._scopes)

    def scope(self, label: str) -> Plan:
        return Plan(
            self.exp_dir,
            self.pipeline_seed,
            _shared=self._shared,
            _scopes=[*self._scopes, label],
        )

    def dir(self, *parts: str) -> str:
        """A path under this plan's (scoped) directory. Not created for you."""
        return str(self.exp_dir.joinpath(*self._scopes, *parts))

    def seed(self, node: str, i: int = 0) -> int:
        """Deterministic seed for (pipeline seed, scope, node, i)."""
        key = f"{self.pipeline_seed}|{self._prefix}{node}|{i}"
        return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)

    def add_stage(
        self,
        name: str,
        deps: Iterable[Handle | str] | Handle | str | None = (),
        *,
        cmds: list[str] | None = None,
        fn: Callable[[], None] | None = None,
        group: str = "",
        value: Any = None,
    ) -> Handle:
        """Adds a stage and returns a Handle for it.

        Give exactly one of `cmds` (shell command lines, run via bash) or `fn`
        (a zero-arg python callable run in-process). `value` is carried on the
        returned Handle for downstream stages to use.
        """
        if (cmds is None) == (fn is None):
            raise ValueError(f"Exactly one of cmds or fn must be given for stage '{name}'.")
        full = self._prefix + name
        if full in self._shared.names:
            raise ValueError(f"Duplicate stage name '{full}'.")
        self._shared.names.add(full)
        self.stages.append(
            Stage(
                name=full,
                deps=dep_names(deps),
                marker=self.exp_dir / ".babydag" / "markers" / f"{full}.done",
                cmds=list(cmds) if cmds is not None else None,
                fn=fn,
                group=self._prefix + group,
                workdir=Path(self.dir()),
            )
        )
        return Handle([full], value)

    def handle(self, *handles: Handle | str, value: Any = None) -> Handle:
        """Merges handles into one, e.g. to depend on all ten policies at once."""
        return Handle(dep_names(handles), value)
