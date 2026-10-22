import pytest

from babydag import Plan


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("BABYDAG_ROOT", str(tmp_path / "root"))
    return tmp_path / "root"


@pytest.fixture
def plan(tmp_path):
    return Plan(tmp_path / "exp" / "Exp_0", seed=0)
