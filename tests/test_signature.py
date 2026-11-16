"""Error signatures, using short made-up lines in the formats seen in real logs."""

from triage.signature import error_signature, log_signature, normalise


def sig(*lines):
    """Signature of a section made of these lines (numbered from 1)."""
    return error_signature(list(enumerate(lines, start=1)))


EXIT = "##[error]Process completed with exit code 1."


def test_pytest_E_line_wins_over_FAILED_line():
    s = sig(
        "FAILED tests/test_cart.py::test_total - AssertionError: assert 2 == 4",
        "E   AssertionError: assert 2 == 4",
        EXIT,
    )
    assert s == "e assertionerror: assert <n> == <n>"
    # Different numbers, same signature.
    assert sig("E   AssertionError: assert 7 == 9") == s


def test_FAILED_line_with_variant_brackets():
    a = sig("FAILED testing/python/raises.py::TestRaises::test_cyclic_reference[function] - assert 2 == 4", EXIT)
    b = sig("FAILED testing/python/raises.py::TestRaises::test_cyclic_reference[method] - assert 2 == 3", EXIT)
    assert a == "failed <path>::testraises::test_cyclic_reference[<param>] - assert <n> == <n>"
    assert a == b   # same test, different variant and numbers: a likely duplicate


def test_zizmor_error_line():
    s = sig(
        "##[group]Run zizmor .github/workflows",
        "  error[unpinned-uses]: unpinned action reference",
        "     --> ./.github/workflows/test.yml:20:9",
        "##[error]Process completed with exit code 14.",
    )
    assert s == "error[unpinned-uses]: unpinned action reference"   # rule name kept


def test_git_remote_ref_line():
    a = sig("fatal: couldn't find remote ref refs/pull/72096/merge", "##[error]Process completed with exit code 128.")
    b = sig("fatal: couldn't find remote ref refs/pull/123/merge", "##[error]Process completed with exit code 128.")
    assert a == "fatal: couldn't find remote ref <path>"
    assert a == b   # different pull request numbers, same cause


def test_setup_python_version_not_found():
    s = sig("##[error]The version '3.11.2' with architecture 'x64' was not found for Ubuntu 26.04.")
    assert s == "the version '<n>' with architecture 'x<n>' was not found for ubuntu <n>."


def test_exit_code_line_alone_gives_no_signature():
    assert sig("some output", EXIT) == ""
    assert sig() == ""


def test_hex_addresses_ids_urls_and_windows_paths():
    assert normalise("Error: segfault at 0x7ffd1a2b in job 132047882191") == "error: segfault at <hex> in job <id>"
    assert normalise("error: see https://github.com/o/r/actions/runs/123") == "error: see <url>"
    assert normalise(r"E   FileNotFoundError: 'C:\\Users\\runner\\Temp\\firefox'") == "e filenotfounderror: '<path>'"


def test_log_signature_uses_the_failed_step_section():
    log = (
        "##[group]Run setup\n"
        "error: an earlier, harmless message outside the failed step\n"
        + "filler\n" * 60 +
        "##[group]Run pytest -q\n"
        "E   ValueError: bad value 3\n"
        "##[error]Process completed with exit code 1.\n"
    )
    assert log_signature(log) == "e valueerror: bad value <n>"
