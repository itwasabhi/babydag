"""A custom template is any object with `render(jobs) -> str`.

    python examples/rl_pipeline.py generate 0 --template custom_template:Makefile

(run from the examples/ dir, or with it on PYTHONPATH, so `custom_template` imports)
"""

from babydag import Job


class Makefile:
    """Emits a Makefile: `make -j` runs independent stages in parallel."""

    def render(self, jobs: list[Job]) -> str:
        lines = ["all: " + " ".join(j.id for j in jobs), ""]
        for j in jobs:
            lines += [f"{j.id}: {' '.join(j.deps)}", f"\t{j.command}"]
        return "\n".join(lines)
