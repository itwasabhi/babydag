"""Policy -> response policy, repeated, then analysis over everything.

    uv run python examples/rl_pipeline.py status 0
    uv run python examples/rl_pipeline.py run 0
    uv run python examples/rl_pipeline.py generate 0 --template slurm

Stand-in shell commands write small files so this runs without any RL code;
swap them for your real training scripts.
"""

from pathlib import Path

import babydag


class PolicyResponse:
    rounds = 10

    def build(self, plan):
        outputs = []
        for i in range(self.rounds):
            r = plan.scope(f"round_{i}")
            pol = r.add_stage(
                "policy",
                cmds=[f"mkdir -p {r.dir('policy')}",
                      f"echo policy seed={r.seed('policy')} > {r.dir('policy')}/policy.txt"],
                value=r.dir("policy"),
            )
            resp = r.add_stage(
                "response",
                deps=[pol],  # a handle is a future: this stage waits for `policy`
                cmds=[f"mkdir -p {r.dir('response')}",
                      f"cat {pol.value}/policy.txt > {r.dir('response')}/response.txt",
                      f"echo response seed={r.seed('response')} >> {r.dir('response')}/response.txt"],
                value=r.dir("response"),
            )
            outputs += [pol, resp]

        def analyze():
            dirs = [Path(h.value) for h in outputs]
            n = sum(1 for d in dirs for f in d.glob("*.txt"))
            print(f"analysis: found {n} output files across {len(dirs)} dirs")

        plan.add_stage("analysis", deps=outputs, fn=analyze)


if __name__ == "__main__":
    babydag.main(PolicyResponse)
