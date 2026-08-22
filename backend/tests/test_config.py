from backend.app.config import Settings


def test_empty_demo_seed_path_disables_seed() -> None:
    assert Settings(demo_seed_path="").demo_seed_path is None
