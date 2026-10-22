import pytest

from babydag import Plan, print_status, select, topo_levels
from babydag.plan import Stage


def diamond(plan):
    a = plan.add_stage("a", cmds=["true"])
    b = plan.add_stage("b", a, cmds=["true"])
    c = plan.add_stage("c", a, cmds=["true"])
    plan.add_stage("d", [b, c], cmds=["true"])
    return {s.name: s for s in plan.stages}


def mark(stage):
    stage.marker.parent.mkdir(parents=True, exist_ok=True)
    stage.marker.touch()


def test_topo_levels_diamond(plan):
    diamond(plan)
    levels = [[s.name for s in lvl] for lvl in topo_levels(plan.stages)]
    assert levels == [["a"], ["b", "c"], ["d"]]


def test_topo_levels_cycle_names_the_cycle(tmp_path):
    def st(name, deps):
        return Stage(name, deps, tmp_path / name, cmds=["true"])

    with pytest.raises(ValueError, match="Cycle detected") as e:
        topo_levels([st("a", ["c"]), st("b", ["a"]), st("c", ["b"]), st("ok", [])])
    assert "a -> c -> b -> a" in str(e.value) or "b -> a -> c -> b" in str(e.value) or "c -> b -> a -> c" in str(e.value)


def test_topo_levels_unknown_dep(plan):
    plan.add_stage("a", deps=["ghost"], cmds=["true"])
    with pytest.raises(ValueError, match=r"(?s)unknown dependencies.*ghost"):
        topo_levels(plan.stages)


def test_select_defaults_to_unfinished(plan):
    by = diamond(plan)
    mark(by["a"])
    assert select(plan.stages) == {"b", "c", "d"}
    assert select(plan.stages, include_finished=True) == {"a", "b", "c", "d"}


def test_select_pattern_pulls_unfinished_upstream_only(plan):
    by = diamond(plan)
    assert select(plan.stages, "^d$") == {"a", "b", "c", "d"}
    mark(by["a"])
    assert select(plan.stages, "^d$") == {"b", "c", "d"}
    mark(by["b"]); mark(by["c"])
    assert select(plan.stages, "^d$") == {"d"}


def test_select_no_deps_stays_narrow(plan):
    diamond(plan)
    assert select(plan.stages, "^d$", with_deps=False) == {"d"}


def test_select_rerun_finished_with_pattern(plan):
    by = diamond(plan)
    for s in by.values():
        mark(s)
    assert select(plan.stages, "^b$") == set()
    assert select(plan.stages, "^b$", include_finished=True) == {"b"}


def test_select_pattern_is_search(plan):
    plan.scope("round_1").add_stage("policy", cmds=["true"])
    plan.scope("round_10").add_stage("policy", cmds=["true"])
    assert select(plan.stages, "round_1:") == {"round_1:policy"}


def test_print_status_glyphs_and_totals(plan, capsys):
    by = diamond(plan)
    mark(by["a"])
    print_status(plan.stages, {"b"}, title="Exp")
    out = capsys.readouterr().out
    assert "✓  a" in out
    assert "→  b" in out and "» →  b" in out
    assert "·  d" in out
    assert "4 stages | 1 done | 2 ready | 1 blocked | 1 selected" in out


def test_print_status_shows_dependencies(plan, capsys):
    diamond(plan)
    print_status(plan.stages)
    out = capsys.readouterr().out
    assert "→  a\n" in out  # a root has no arrow after it
    assert "b  ← a" in out and "c  ← a" in out
    assert "d  ← b, c" in out
    assert "a  ←" not in out


def test_print_status_truncates_long_dep_lists(plan, capsys):
    roots = [plan.add_stage(f"s{i}", cmds=["true"]) for i in range(6)]
    plan.add_stage("report", roots, cmds=["true"])
    print_status(plan.stages)
    assert "report  ← s0, s1, s2, +3 more" in capsys.readouterr().out
