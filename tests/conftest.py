"""Network-test switches and a guard against CI passing with nothing tested.

* Tests marked ``network`` talk to the live internet (badssl.com, dodo.ac). They
  are skipped unless NETWORK_TESTS=1.
* With REQUIRE_NETWORK_TESTS=1 (set in CI) the run fails if no network test
  actually passed. pytest exits 0 when everything selected is skipped, so a lost
  environment variable would otherwise show a green check that tested nothing.
* On GitHub Actions the outcome counts are written as a workflow annotation, so
  they can be checked from the run page or the public API without log access.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import pytest

_outcomes: Counter[str] = Counter()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="network test; set NETWORK_TESTS=1 to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


def pytest_runtest_logreport(report):
    is_network = "network" in report.keywords
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        _outcomes[report.outcome] += 1
        if is_network:
            _outcomes[f"network_{report.outcome}"] += 1


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("REQUIRE_NETWORK_TESTS") == "1" and _outcomes["network_passed"] == 0:
        print("\nREQUIRE_NETWORK_TESTS=1 but no network test passed; failing the run.")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(
            f"\n::notice title=pytest on {sys.platform}::"
            f"{_outcomes['passed']} passed, {_outcomes['failed']} failed, "
            f"{_outcomes['skipped']} skipped; network tests passed: {_outcomes['network_passed']}, "
            f"skipped: {_outcomes['network_skipped']}"
        )
