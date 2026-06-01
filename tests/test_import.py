import sceneapi


def test_import_exposes_version() -> None:
    assert isinstance(sceneapi.__version__, str)
