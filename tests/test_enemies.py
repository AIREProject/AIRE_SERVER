from app.brain.command_intent import ENEMY_PATTERN
from app.brain.enemies import EnemyRepository
from app.gamedata.dataset import ENEMIES


def test_every_enemy_name_is_lookupable() -> None:
    repository = EnemyRepository()

    for enemy in ENEMIES:
        fact = repository.fact_for(f"{enemy.name_ko} 약점이 뭐야?")

        assert fact is not None, enemy.enemy_id
        assert fact.kind == "enemy"
        assert enemy.name_ko in fact.text
        assert enemy.weak_part in fact.text


def test_every_enemy_alias_is_lookupable() -> None:
    repository = EnemyRepository()

    for enemy in ENEMIES:
        for alias in enemy.aliases:
            fact = repository.fact_for(f"{alias} 공략법 알려 줘")

            assert fact is not None, (enemy.enemy_id, alias)
            assert enemy.weak_part in fact.text


def test_enemy_weak_element_names_are_all_supported() -> None:
    repository = EnemyRepository()
    names = repository.weak_element_names()

    for enemy in ENEMIES:
        fact = repository.fact_for(enemy.name_ko)

        assert fact is not None
        assert names[enemy.weak_element] in fact.text


def test_weak_part_takes_the_particle_its_ending_requires() -> None:
    """받침 유무를 무시하면 '가슴의 깨진 코어이 약점이고' 같은 문장이 대사로 나간다."""

    repository = EnemyRepository()

    with_batchim = repository.fact_for("참호병 약점")
    without_batchim = repository.fact_for("골리앗 약점")

    assert with_batchim is not None
    assert "다리 관절이 약점이고" in with_batchim.text
    assert without_batchim is not None
    assert "가슴의 깨진 코어가 약점이고" in without_batchim.text


def test_multiple_enemy_names_do_not_choose_one() -> None:
    repository = EnemyRepository()

    assert repository.fact_for("골리앗과 참호병 약점이 뭐야?") is None


def test_enemy_alias_does_not_match_inside_another_word() -> None:
    repository = EnemyRepository()

    assert repository.fact_for("골리앗과자 약점이 뭐야?") is None


def test_mock_enemy_router_accepts_every_enemy_alias() -> None:
    for enemy in ENEMIES:
        for alias in enemy.aliases:
            assert ENEMY_PATTERN.search(f"{alias} 어떻게 잡아?") is not None


def test_resolve_target_finds_every_enemy_by_name_or_alias() -> None:
    repository = EnemyRepository()

    for enemy in ENEMIES:
        assert repository.resolve_target(f"{enemy.name_ko} 공격해") == enemy.enemy_id
        for alias in enemy.aliases:
            assert repository.resolve_target(f"{alias} 공격해") == enemy.enemy_id


def test_resolve_target_leaves_the_game_to_decide_when_unspecified() -> None:
    repository = EnemyRepository()

    assert repository.resolve_target("공격해") is None


def test_resolve_target_does_not_guess_among_multiple_enemies() -> None:
    repository = EnemyRepository()

    assert repository.resolve_target("골리앗과 참호병 공격해") is None
