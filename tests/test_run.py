import subprocess

import pytest

from babydag import Plan, run_local, select


def run(plan, pattern=None, **kw):
    run_local(plan.stages, select(plan.stages, pattern, **{k: v for k, v in kw.items() if k in ("include_finished", "with_deps")}),
              **{k: v for k, v in kw.items() if k in ("dry_run", "show")})


def test_cmds_run_and_leave_marker(plan, tmp_path):
    out = tmp_path / "out.txt"
    plan.add_stage("a", cmds=[f"echo hi > {out}"])
    run(plan)
    assert out.read_text() == "hi\n"
    assert plan.stages[0].done()


def test_fn_stage_runs_in_process(plan):
    calls = []
    plan.add_stage("a", fn=lambda: calls.append(1))
    run(plan)
    assert calls == [1] and plan.stages[0].done()


def test_second_run_executes_nothing(plan, tmp_path):
    log = tmp_path / "log"
    plan.add_stage("a", cmds=[f"echo a >> {log}"])
    run(plan)
    run(plan)
    assert log.read_text() == "a\n"


def test_rerun_reexecutes_finished_stage(plan, tmp_path):
    log = tmp_path / "log"
    plan.add_stage("a", cmds=[f"echo a >> {log}"])
    run(plan)
    run(plan, include_finished=True)
    assert log.read_text() == "a\na\n"


def test_failure_raises_leaves_no_marker_and_blocks_downstream(plan, tmp_path):
    log = tmp_path / "log"
    bad = plan.add_stage("bad", cmds=["exit 3"])
    plan.add_stage("after", bad, cmds=[f"echo after >> {log}"])
    with pytest.raises(subprocess.CalledProcessError):
        run(plan)
    assert not plan.stages[0].done()
    assert not log.exists()


def test_failure_in_cmds_midway_means_no_marker(plan):
    plan.add_stage("a", cmds=["false", "echo unreachable"])
    with pytest.raises(subprocess.CalledProcessError):
        run(plan)
    assert not plan.stages[0].done()


def test_fn_exception_propagates_without_marker(plan):
    def boom():
        raise RuntimeError("boom")

    plan.add_stage("a", fn=boom)
    with pytest.raises(RuntimeError, match="boom"):
        run(plan)
    assert not plan.stages[0].done()


def test_resume_after_failure_runs_only_the_rest(plan, tmp_path):
    log, gate = tmp_path / "log", tmp_path / "gate"
    a = plan.add_stage("a", cmds=[f"echo a >> {log}"])
    plan.add_stage("b", a, cmds=[f"test -e {gate}", f"echo b >> {log}"])
    with pytest.raises(subprocess.CalledProcessError):
        run(plan)
    gate.touch()
    run(plan)
    assert log.read_text() == "a\nb\n"


def test_dry_run_and_show_execute_nothing(plan, tmp_path, capsys):
    out = tmp_path / "out"
    plan.add_stage("a", cmds=[f"touch {out}"])
    run(plan, dry_run=True)
    run(plan, show=True)
    assert not out.exists() and not plan.stages[0].done()
    assert f"touch {out}" in capsys.readouterr().out


def test_runs_in_dependency_order_even_if_added_out_of_order(plan, tmp_path):
    log = tmp_path / "log"
    plan.add_stage("second", deps=["first"], cmds=[f"echo second >> {log}"])
    plan.add_stage("first", cmds=[f"echo first >> {log}"])
    run(plan)
    assert log.read_text() == "first\nsecond\n"


def test_downstream_reads_upstream_output_via_handle_value(plan, tmp_path):
    pol = plan.add_stage(
        "policy", cmds=[f"mkdir -p {tmp_path}/pol", f"echo weights > {tmp_path}/pol/w.txt"],
        value=str(tmp_path / "pol"),
    )
    got = []
    plan.add_stage("analysis", pol, fn=lambda: got.append((tmp_path / "pol" / "w.txt").read_text()))
    run(plan)
    assert got == ["weights\n"]
