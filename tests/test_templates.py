import shlex
import sys
import textwrap

import pytest

from babydag import FileTemplate, Job, ShellTemplate, SlurmTemplate, build_plan, main
from babydag.templates import build_jobs, load_template


class Fan:
    n = 3

    def build(self, plan):
        roots = [plan.add_stage(f"s_{i}", cmds=["true"]) for i in range(self.n)]
        plan.add_stage("report", roots, cmds=["true"])


def jobs_for(bundle=1, selected=None, **params):
    plan = build_plan(Fan, params)
    selected = selected or {s.name for s in plan.stages}
    return build_jobs(plan.stages, selected, lambda names: "run " + ",".join(names), bundle)


def test_build_jobs_one_per_stage_with_deps():
    jobs = jobs_for()
    assert [j.id for j in jobs] == ["JOB_0", "JOB_1", "JOB_2", "JOB_3"]
    assert [j.deps for j in jobs] == [[], [], [], ["JOB_0", "JOB_1", "JOB_2"]]
    assert jobs[3].stages == ["report"] and jobs[3].command == "run report"


def test_build_jobs_bundle_packs_each_level():
    jobs = jobs_for(bundle=2)
    assert [j.stages for j in jobs] == [["s_0", "s_1"], ["s_2"], ["report"]]
    assert jobs[2].deps == ["JOB_0", "JOB_1"]
    assert jobs[0].name == "s_0+1"


def test_build_jobs_drops_deps_outside_selection():
    jobs = jobs_for(selected={"report"})
    assert len(jobs) == 1 and jobs[0].deps == []


def test_bad_bundle():
    with pytest.raises(ValueError):
        jobs_for(bundle=0)


def test_shell_template_runs_in_order():
    out = ShellTemplate().render(jobs_for())
    assert out.startswith("#!/bin/bash")
    assert out.index("run s_0") < out.index("run report")


def test_slurm_template_dependencies():
    out = SlurmTemplate(sbatch_args="--partition=gpu", preamble="module load uv").render(jobs_for())
    lines = out.splitlines()
    assert "module load uv" in lines
    root = next(l for l in lines if l.startswith("JOB_0="))
    assert "--dependency" not in root and "--partition=gpu" in root and "--parsable" in root
    last = next(l for l in lines if l.startswith("JOB_3="))
    assert "--dependency=afterok:$JOB_0:$JOB_1:$JOB_2" in last
    assert "--wrap='run report'" in last or '--wrap="run report"' in last or "--wrap=" in last


def test_file_template_substitution(tmp_path):
    path = tmp_path / "my.tmpl"
    path.write_text(textwrap.dedent("""\
        #!/bin/bash
        # ${n_jobs} jobs
        %%job
        ${id}=$$(mysub ${dependency_flag} --name ${name} --cmd ${command_quoted})
        # deps: ${dependency_ids}
        """))
    out = FileTemplate(path).render(jobs_for())
    assert "# 4 jobs" in out
    assert "JOB_0=$(mysub  --name s_0 --cmd 'run s_0')" in out
    assert "JOB_3=$(mysub --dependency=afterok:$JOB_0:$JOB_1:$JOB_2 --name report" in out
    assert "# deps: $JOB_0:$JOB_1:$JOB_2" in out


def test_file_template_needs_separator(tmp_path):
    path = tmp_path / "bad.tmpl"
    path.write_text("no separator here")
    with pytest.raises(ValueError, match="%%job"):
        FileTemplate(path)


def test_custom_template_object_is_accepted():
    class Mine:
        def render(self, jobs: list[Job]) -> str:
            return "|".join(j.id for j in jobs)

    assert Mine().render(jobs_for()) == "JOB_0|JOB_1|JOB_2|JOB_3"


class _Custom:
    def render(self, jobs):
        return f"custom:{len(jobs)}"


def test_load_template_variants(tmp_path):
    assert isinstance(load_template("slurm"), SlurmTemplate)
    assert isinstance(load_template("shell"), ShellTemplate)
    f = tmp_path / "t.tmpl"
    f.write_text("h\n%%job\nj\n")
    assert isinstance(load_template(str(f)), FileTemplate)
    assert load_template(f"{__name__}:_Custom").render([]) == "custom:0"
    with pytest.raises(ValueError, match="Unknown template"):
        load_template("nonsense")


def test_cli_generate_commands_carry_seed_params_and_anchored_regex(capsys):
    main(Fan, ["generate", "7", "--n", "12", "--template", "shell", "--python", "uvpy"])
    out = capsys.readouterr().out
    cmd = next(l for l in out.splitlines() if "s_1'" in l or "s_1)" in l)
    assert cmd.startswith("uvpy ") and " run 7 " in cmd
    assert "--n=12" in cmd and "--no-deps" in cmd
    pattern = shlex.split(cmd)[-1]
    assert pattern == "^(s_1)$"  # anchored, so s_1 can't match s_10 / s_11


def test_cli_generate_nothing_selected(capsys):
    main(Fan, ["run", "0", "--n", "1"])
    capsys.readouterr()
    main(Fan, ["generate", "0", "--n", "1"])
    cap = capsys.readouterr()
    assert cap.out == "" and "Nothing to generate" in cap.err


class WithDefaults(Fan):
    def template(self):
        return SlurmTemplate(sbatch_args="--partition=mine")

    def python(self):
        return "uv run python"


def test_experiment_defaults_for_template_and_python(capsys):
    main(WithDefaults, ["generate", "0"])
    out = capsys.readouterr().out
    assert "sbatch --parsable" in out and "--partition=mine" in out
    assert "uv run python " in out


def test_cli_flags_override_experiment_defaults(capsys):
    main(WithDefaults, ["generate", "0", "--template", "shell", "--python", "py3"])
    out = capsys.readouterr().out
    assert "sbatch" not in out and "py3 " in out and "uv run" not in out


def test_default_template_is_shell_and_python_is_python(capsys):
    main(Fan, ["generate", "0"])
    out = capsys.readouterr().out
    assert "sbatch" not in out and "\npython " in out
