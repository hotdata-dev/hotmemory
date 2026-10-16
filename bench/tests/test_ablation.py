import pytest

from bench.ablation import ABLATIONS, ARMS, ArmStore, arm_query
from bench.retrieval import ABLATED
from hotmemory.hotdata import EPISODE_TABLE, MEMORY_TABLE, _fused_query

TABLES = (MEMORY_TABLE, EPISODE_TABLE)
VECTOR = "ARRAY[0.1, 0.2]"


def test_every_arm_gives_the_fused_query() -> None:
    assert arm_query(TABLES, "TRUE", VECTOR, '"cat"', 5, ARMS) == _fused_query(
        TABLES, "TRUE", VECTOR, '"cat"', 5
    )


@pytest.mark.parametrize("name", list(ABLATIONS))
def test_an_ablation_leaves_out_its_arms(name: str) -> None:
    sql = arm_query(TABLES, "TRUE", VECTOR, '"cat"', 5, ABLATIONS[name])
    assert ("bm25_search" in sql) == ("bm25" in ABLATIONS[name])
    assert ("content_rank AS" in sql) == ("content" in ABLATIONS[name])
    assert ("cue_rank AS" in sql) == ("cue" in ABLATIONS[name])


def test_the_runner_names_each_ablation() -> None:
    assert tuple(ABLATIONS) == ABLATED


@pytest.mark.parametrize("arms", [(), ("bm25", "title")])
def test_arms_must_be_a_subset(arms: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="arms must be"):
        ArmStore(None, "db", embedder=lambda texts: [], model="m", dimensions=2, arms=arms)
