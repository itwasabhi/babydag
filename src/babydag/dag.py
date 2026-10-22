"""Graph utilities and local execution for lists of Stages."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from collections.abc import Collection

from .plan import Stage


def _format_graph_error(stuck: list[Stage], all_names: set[str]) -> str:
    missing = [
        f"  {s.name} -> {d!r} (not in plan)"
        for s in stuck
        for d in s.deps
        if d not in all_names
    ]
    if missing:
        return "Stage DAG has unknown dependencies:\n" + "\n".join(missing)

    deps = {s.name: s.deps for s in stuck}
    # Every stuck stage waits on another stuck stage, so walking dep links must
    # eventually revisit one: that revisit closes a cycle.
    seen: list[str] = []
    node = stuck[0].name
    while node not in seen:
        seen.append(node)
        node = next(d for d in deps[node] if d in deps)
    cycle = [*seen[seen.index(node) :], node]
    return "Cycle detected in stage DAG: " + " -> ".join(cycle)


def topo_levels(stages: list[Stage]) -> list[list[Stage]]:
    """Groups stages into levels; everything in a level can run in parallel.

    Raises ValueError on a cycle or a dependency that isn't in `stages`.
    """
    all_names = {s.name for s in stages}
    resolved: set[str] = set()
    levels: list[list[Stage]] = []
    remaining = list(stages)
    while remaining:
        ready = [s for s in remaining if all(d in resolved for d in s.deps)]
        if not ready:
            raise ValueError(_format_graph_error(remaining, all_names))
        resolved.update(s.name for s in ready)
        levels.append(ready)
        remaining = [s for s in remaining if s.name not in resolved]
    return levels


def select(
    stages: list[Stage],
    pattern: str | None = None,
    *,
    include_finished: bool = False,
    with_deps: bool = True,
) -> set[str]:
    """Picks the stage names to run.

    By default: unfinished stages, narrowed to names matching the regex
    `pattern` (re.search). `include_finished` also selects done stages (to rerun
    them). `with_deps` additionally pulls in unfinished upstream stages, so a
    narrow pattern doesn't fail on a missing input.
    """
    chosen = {s.name for s in stages if include_finished or not s.done()}
    if pattern is not None:
        chosen &= {s.name for s in stages if re.search(pattern, s.name)}
    if with_deps:
        by_name = {s.name: s for s in stages}
        queue = list(chosen)
        while queue:
            for dep in by_name[queue.pop()].deps:
                if dep in by_name and dep not in chosen and not by_name[dep].done():
                    chosen.add(dep)
                    queue.append(dep)
    return chosen


def _deps_suffix(deps: list[str], limit: int = 3) -> str:
    if not deps:
        return ""
    shown = ", ".join(deps[:limit])
    extra = f", +{len(deps) - limit} more" if len(deps) > limit else ""
    return f"  ← {shown}{extra}"


def print_status(
    stages: list[Stage], selected: Collection[str] = (), title: str = ""
) -> None:
    """Prints each stage as done (✓), ready (→) or blocked (·), grouped by `group`,
    with the stages it depends on after ←."""
    done = {s.name for s in stages if s.done()}
    ready = {s.name for s in stages if s.name not in done and all(d in done for d in s.deps)}
    if title:
        print(f"\n{title}")
    print("=" * 72)
    groups: dict[str, list[Stage]] = {}
    for s in stages:
        groups.setdefault(s.group, []).append(s)
    for label, members in groups.items():
        print(f"\n  {label}" if label else "")
        for s in members:
            sym = "✓" if s.name in done else "→" if s.name in ready else "·"
            sel = "»" if s.name in selected else " "
            print(f"    {sel} {sym}  {s.name}{_deps_suffix(s.deps)}")
    n_blocked = len(stages) - len(done) - len(ready)
    print("\n" + "=" * 72)
    print(
        f"  {len(stages)} stages | {len(done)} done | {len(ready)} ready"
        f" | {n_blocked} blocked | {len(set(selected))} selected\n"
    )


def run_script(script: str) -> None:
    """Runs bash `script` in its own process group; Ctrl-C terminates the group."""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        proc = subprocess.Popen(["bash", path], start_new_session=True)
        try:
            proc.wait()
        except KeyboardInterrupt:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait()
            raise
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, ["bash", path])
    finally:
        os.unlink(path)


def run_local(
    stages: list[Stage],
    selected: Collection[str],
    *,
    dry_run: bool = False,
    show: bool = False,
) -> None:
    """Runs the selected stages in dependency order, stopping at the first failure.

    A stage's marker is only written after it succeeds, so rerunning resumes
    where a failed run left off. `show` prints what would run, `dry_run` does
    nothing.
    """
    order = [s for level in topo_levels(stages) for s in level]
    for stage in order:
        if stage.name not in selected:
            continue
        if show:
            print(f"### {stage.name} ###")
            print(stage.script() if stage.cmds is not None else "[python fn, in-process]")
        if show or dry_run:
            continue
        if stage.fn is not None:
            stage.fn()
            stage.marker.parent.mkdir(parents=True, exist_ok=True)
            stage.marker.touch()
        else:
            run_script(stage.script())
