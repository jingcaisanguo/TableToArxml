"""External documentation must be self-contained and use the canonical layout."""

import re

from helpers import ROOT


def test_documentation_file_links_exist():
    documents = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md"), *(ROOT / "test").rglob("*.md")]
    for document in documents:
        for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            target = match.group(1).strip("<>")
            if target.startswith(("http:", "https:", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            assert (document.parent / target).exists(), (document, target)


def test_external_documentation_does_not_link_outside_delivery():
    docs = ROOT / "docs" / "user"
    for document in docs.rglob("*.md"):
        for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            target = match.group(1).split("#", 1)[0]
            if not target or target.startswith(("https:", "http:", "mailto:")):
                continue
            assert docs in (document.parent / target).resolve().parents, (document, target)


def test_external_documentation_uses_only_canonical_paths():
    docs = ROOT / "docs" / "user"
    assert {path.name for path in docs.iterdir()} == {"README.md", "migration.md", "swc", "system"}
    assert list(docs.rglob("SwcTemplate.xlsx")) == [docs / "swc" / "SwcTemplate.xlsx"]
