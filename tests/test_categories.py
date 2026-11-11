from triage.categories import CATEGORIES, CATEGORY_NAMES

EXPECTED = {
    "dependency_install", "test_failure", "compile_build_error", "lint_format",
    "timeout_flaky", "network_infra", "config_permissions", "unknown",
}


def test_exactly_the_eight_expected_categories():
    assert set(CATEGORY_NAMES) == EXPECTED


def test_every_category_has_description_and_next_step():
    for name, info in CATEGORIES.items():
        assert info["description"].strip(), name
        assert info["next_step"].strip(), name
