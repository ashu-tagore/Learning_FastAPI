import atlas


def test_package_imports_and_has_a_version() -> None:
    assert atlas.__version__ == "0.1.0"
