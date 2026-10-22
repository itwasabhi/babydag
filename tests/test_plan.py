import pytest

from babydag import Handle, Plan


def test_scope_prefixes_names_dirs_and_nests(plan):
    r = plan.scope("round_1").scope("env_a")
    h = r.add_stage("train", cmds=["true"])
    assert h.names == ["round_1:env_a:train"]
    assert r.dir("out").endswith("Exp_0/round_1/env_a/out")
    assert r.name == "Exp_0_round_1_env_a"
    assert plan.dir("x").endswith("Exp_0/x")


def test_scoped_plans_share_one_stage_list(plan):
    plan.scope("a").add_stage("s", cmds=["true"])
    plan.scope("b").add_stage("s", cmds=["true"])
    plan.add_stage("root", cmds=["true"])
    assert [s.name for s in plan.stages] == ["a:s", "b:s", "root"]


def test_same_name_in_different_scopes_is_fine_but_duplicates_raise(plan):
    plan.add_stage("s", cmds=["true"])
    plan.scope("a").add_stage("s", cmds=["true"])
    with pytest.raises(ValueError, match="Duplicate stage name 's'"):
        plan.add_stage("s", cmds=["true"])


def test_seed_is_deterministic_and_varies(tmp_path):
    a = Plan(tmp_path, seed=0)
    assert a.seed("pop", 1) == Plan(tmp_path, seed=0).seed("pop", 1)
    seeds = {
        a.seed("pop", 0), a.seed("pop", 1), a.seed("br", 0),
        a.scope("x").seed("pop", 0), Plan(tmp_path, seed=1).seed("pop", 0),
    }
    assert len(seeds) == 5


def test_deps_accept_handles_strings_and_mixes(plan):
    a = plan.add_stage("a", cmds=["true"])
    b = plan.add_stage("b", cmds=["true"])
    plan.add_stage("c", deps=a, cmds=["true"])
    plan.add_stage("d", deps=[a, "b"], cmds=["true"])
    plan.add_stage("e", deps=[plan.handle(a, b)], cmds=["true"])
    by = {s.name: s for s in plan.stages}
    assert by["c"].deps == ["a"]
    assert by["d"].deps == ["a", "b"]
    assert by["e"].deps == ["a", "b"]
    assert by["a"].deps == []


def test_bad_dep_type_raises(plan):
    with pytest.raises(TypeError):
        plan.add_stage("a", deps=[42], cmds=["true"])


def test_handle_value_and_merge(plan):
    a = plan.add_stage("a", cmds=["true"], value="/out/a")
    assert a == Handle(["a"], "/out/a")
    assert plan.handle(a, "z", value=1) == Handle(["a", "z"], 1)


@pytest.mark.parametrize("kwargs", [{}, {"cmds": ["true"], "fn": lambda: None}])
def test_exactly_one_of_cmds_or_fn(plan, kwargs):
    with pytest.raises(ValueError, match="Exactly one"):
        plan.add_stage("a", **kwargs)


def test_script_runs_cmds_in_order_then_touches_marker(plan):
    plan.add_stage("a", cmds=["echo one", "echo two"])
    lines = plan.stages[0].script().splitlines()
    assert lines[:2] == ["#!/bin/bash", "set -e"]
    assert lines[2] == f'mkdir -p "{plan.exp_dir}"'  # the stage's dir always exists
    assert lines[3:5] == ["echo one", "echo two"]
    assert lines[-1] == f'touch "{plan.stages[0].marker}"'


def test_fn_stage_has_no_script(plan):
    plan.add_stage("a", fn=lambda: None)
    with pytest.raises(ValueError):
        plan.stages[0].script()


def test_group_gets_scope_prefix(plan):
    plan.scope("r0").add_stage("a", cmds=["true"], group="train")
    assert plan.stages[0].group == "r0:train"


def test_scoped_stage_script_creates_its_scope_dir(plan):
    plan.scope("r0").add_stage("a", cmds=["true"])
    assert f'mkdir -p "{plan.exp_dir}/r0"' in plan.stages[0].script()
