import sys
from pathlib import Path

import pytest

from babydag import build_plan, main

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))
import rl_pipeline  # noqa: E402


class Params:
    count = 2
    rate = 0.5
    name = "x"
    flag = False
    items = ["a", "b"]
    sizes = [1, 2]
    maybe = None

    def build(self, plan):
        self.seen = None
        plan.add_stage("only", fn=lambda: SEEN.append(
            (self.count, self.rate, self.name, self.flag, self.items, self.sizes, self.maybe)))


SEEN = []


def test_params_parsed_by_default_type():
    SEEN.clear()
    main(Params, ["run", "0", "--count", "5", "--rate", "1.5", "--name", "y", "--flag", "true",
                  "--items", "p,q,r", "--sizes", "3,4", "--maybe", "m"])
    assert SEEN == [(5, 1.5, "y", True, ["p", "q", "r"], [3, 4], "m")]


def test_defaults_used_when_flags_absent():
    SEEN.clear()
    main(Params, ["run", "0"])
    assert SEEN == [(2, 0.5, "x", False, ["a", "b"], [1, 2], None)]


def test_bad_param_value_and_unknown_param_exit():
    with pytest.raises(SystemExit):
        main(Params, ["status", "0", "--count", "notanint"])
    with pytest.raises(SystemExit):
        main(Params, ["status", "0", "--nope", "1"])


def test_build_plan_rejects_unknown_param():
    with pytest.raises(ValueError, match="Unknown param 'nope'"):
        build_plan(Params, {"nope": 1})


def test_default_experiment_dir_and_override(root):
    assert build_plan(Params, seed=3).exp_dir == root / "Params" / "Params_3"

    class Custom:
        def experiment_dir(self, seed):
            return f"mine/run{seed}"

        def build(self, plan):
            pass

    assert build_plan(Custom, seed=4).exp_dir == root / "mine" / "run4"


def test_status_prints_and_changes_nothing(root, capsys):
    main(rl_pipeline.PolicyResponse, ["status", "0", "--rounds", "2"])
    out = capsys.readouterr().out
    assert "5 stages | 0 done" in out
    assert not root.exists()


def test_full_rl_pipeline_runs_and_resumes(root, capsys):
    cls = rl_pipeline.PolicyResponse
    main(cls, ["run", "0"])
    out = capsys.readouterr().out
    assert "21 stages | 0 done" in out
    assert "analysis: found 20 output files across 20 dirs" in out

    main(cls, ["status", "0"])
    assert "21 stages | 21 done" in capsys.readouterr().out

    main(cls, ["run", "0"])
    out = capsys.readouterr().out
    assert "Nothing to run." in out and "analysis:" not in out


def test_only_with_rerun_reruns_just_that_stage(root, capsys):
    cls = rl_pipeline.PolicyResponse
    main(cls, ["run", "0", "--rounds", "2"])
    capsys.readouterr()
    resp = root / "PolicyResponse" / "PolicyResponse_0" / "round_1" / "response" / "response.txt"
    resp.unlink()
    main(cls, ["run", "0", "--rounds", "2", "--only", "^round_1:response$", "--rerun", "--no-deps"])
    out = capsys.readouterr().out
    assert "1 selected" in out
    assert resp.exists()


def test_only_pulls_in_unfinished_deps(root, capsys):
    main(rl_pipeline.PolicyResponse, ["run", "0", "--rounds", "2", "--only", "^round_1:response$"])
    out = capsys.readouterr().out
    assert "2 selected" in out  # response + its policy, nothing else
    main(rl_pipeline.PolicyResponse, ["status", "0", "--rounds", "2"])
    assert "2 done" in capsys.readouterr().out


def test_fresh_wipes_experiment_dir(root, capsys):
    cls = rl_pipeline.PolicyResponse
    main(cls, ["run", "0", "--rounds", "1"])
    exp = root / "PolicyResponse" / "PolicyResponse_0"
    stray = exp / "stray.txt"
    stray.write_text("x")
    main(cls, ["run", "0", "--rounds", "1", "--fresh", "--dry-run"])
    assert stray.exists()
    main(cls, ["run", "0", "--rounds", "1", "--fresh"])
    assert not stray.exists() and (exp / "round_0" / "policy" / "policy.txt").exists()
    assert "3 stages | 0 done" in capsys.readouterr().out.split("--fresh: removing")[-1]


def test_other_seeds_are_isolated(root, capsys):
    cls = rl_pipeline.PolicyResponse
    main(cls, ["run", "0", "--rounds", "1"])
    capsys.readouterr()
    main(cls, ["status", "1", "--rounds", "1"])
    assert "3 stages | 0 done" in capsys.readouterr().out


def test_script_entrypoint_end_to_end(root):
    import subprocess
    env = {**__import__("os").environ, "BABYDAG_ROOT": str(root)}
    script = str(Path(rl_pipeline.__file__))
    r = subprocess.run([sys.executable, script, "run", "0", "--rounds", "2"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "analysis: found 4 output files" in r.stdout
    g = subprocess.run([sys.executable, script, "generate", "0", "--rounds", "2", "--rerun", "--template", "shell"],
                       env=env, capture_output=True, text=True)
    assert g.returncode == 0, g.stderr
    assert script in g.stdout and "--rounds=2" in g.stdout


def test_dinner_example_runs_in_order_and_resumes(root, capsys):
    sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))
    import dinner

    main(dinner.Dinner, ["run", "0", "--veggie", "onion"])
    out = capsys.readouterr().out
    assert "4 stages | 0 done" in out
    assert out.rstrip().endswith("cooked chopped onion")
    main(dinner.Dinner, ["run", "0", "--veggie", "onion"])
    assert "Nothing to run." in capsys.readouterr().out
    main(dinner.Dinner, ["run", "0", "--only", "^cook", "--rerun", "--no-deps"])
    assert "1 selected" in capsys.readouterr().out
