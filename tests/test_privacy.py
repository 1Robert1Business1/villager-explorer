"""The repo is public: no personal details in outbound identifiers or tracked files."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

import src.images as images

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_dataset  # noqa: E402

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Fine in a public repo: git's no-reply forms, and RFC 2606 reserved test domains
# (used by the hostile-input fixtures). Never a person's inbox.
ALLOWED_EMAILS = re.compile(
    r"(@users\.noreply\.github\.com|noreply@anthropic\.com|\.(example|invalid|test))$"
)
URL_USERINFO = re.compile(r"://[^/\s@]*@")  # "https://user:pw@host" is a URL, not an address


@pytest.mark.parametrize("user_agent", [images.USER_AGENT, build_dataset.USER_AGENT])
def test_user_agents_identify_the_project_not_a_person(user_agent):
    assert not EMAIL.search(user_agent), user_agent
    assert "github.com/1Robert1Business1/villager-explorer" in user_agent


def _tracked_text_files():
    try:
        names = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    for name in names:
        path = ROOT / name
        if path.suffix.lower() in {".png", ".jpg", ".gif", ".webp"}:
            continue
        yield name, path.read_text(encoding="utf-8", errors="ignore")


def test_no_personal_email_addresses_in_tracked_files():
    found = [
        (name, match)
        for name, content in _tracked_text_files()
        for match in EMAIL.findall(URL_USERINFO.sub("://", content))
        if not ALLOWED_EMAILS.search(match)
    ]
    assert not found, found
