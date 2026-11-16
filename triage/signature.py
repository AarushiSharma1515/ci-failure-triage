"""Error signature: a short, normalised copy of a log's main error line.

Two logs with the same signature very likely failed for the same reason, so the
labelling tool can warn "LIKELY DUPLICATE". It is only a hint: a human still decides.

Which line is used (searched inside the failed-step section, see triage/sections.py):
  1. the first pytest reason line, starting with "E   "
  2. otherwise the first line starting with "FAILED " or "ERROR " (pytest's summary)
  3. otherwise the first line containing "error:" (also "error[code]:", as written by
     zizmor and rustc), "fatal:" (git) or "##[error]" (GitHub), but never
     "Process completed with exit code ..." (that only says the step failed, not why).
If no line matches, the signature is "" (no signature).

How the line is normalised, so that small differences don't hide a duplicate:
  lowercase; URLs -> <url>; hex addresses 0x... -> <hex>;
  pytest parameters ::test_x[...] -> ::test_x[<param>]; file paths -> <path>;
  long numbers such as run/job IDs (6+ digits) -> <id>; other numbers -> <n>;
  runs of whitespace -> one space.
"""

import re

from triage.clean import prepare_lines
from triage.sections import extract_failed_section

# The three kinds of error line, in priority order.
ERROR_LINE_PATTERNS = [
    re.compile(r"^E   "),                                                     # 1. pytest reason
    re.compile(r"^(FAILED|ERROR) "),                                          # 2. pytest summary
    re.compile(r"error(\[[^\]]*\])?:|fatal:|##\[error\]", re.IGNORECASE),     # 3. other tools
]
NOT_A_CAUSE = "Process completed with exit code"

URL_RE = re.compile(r"https?://\S+")
HEX_RE = re.compile(r"0x[0-9a-f]+")
# "::name[" then the shortest text up to a "]" that is followed by a space or the line end,
# so nested brackets like test_x[a-[1]] are handled.
PARAM_RE = re.compile(r"(::[\w.]+)\[.*?\](?=\s|$)")
# Optional drive letter, then path parts separated by / or \ (e.g. tests/a.py, C:\\x\\y, refs/pull/1/merge).
PATH_RE = re.compile(r"(?:[a-z]:)?[\w.~-]*(?:[/\\]+[\w.~@+-]+)+")
ID_RE = re.compile(r"\d{6,}")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")    # 4, 2.5 and 3.11.2 all become <n>
SPACES_RE = re.compile(r"\s+")


def find_error_line(section: list[tuple[int, str]]) -> str | None:
    """The main error line of a section, or None. See the module docstring for the order."""
    for pattern in ERROR_LINE_PATTERNS:
        for _, text in section:
            if pattern.search(text) and NOT_A_CAUSE not in text:
                return text
    return None


def normalise(line: str) -> str:
    """Replace the parts of an error line that change between runs with placeholders."""
    text = line.strip().lower().replace("##[error]", "")
    text = URL_RE.sub("<url>", text)          # first: URLs contain paths and IDs
    text = HEX_RE.sub("<hex>", text)          # before numbers: 0x1f contains digits
    text = PARAM_RE.sub(r"\1[<param>]", text) # before paths: parameters can contain "/"
    text = PATH_RE.sub("<path>", text)
    text = ID_RE.sub("<id>", text)            # before NUMBER_RE, which would also match IDs
    text = NUMBER_RE.sub("<n>", text)
    return SPACES_RE.sub(" ", text).strip()


def error_signature(section: list[tuple[int, str]]) -> str:
    """Signature of a failed-step section ("" if it has no recognisable error line)."""
    line = find_error_line(section)
    return normalise(line) if line is not None else ""


def log_signature(text: str) -> str:
    """Signature of a whole log: find its failed-step section first, then its error line."""
    section, _ = extract_failed_section(prepare_lines(text))
    return error_signature(section)
