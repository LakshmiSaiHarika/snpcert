"""Tests for scripts/os_lifecycle.py. Run: python3 -m unittest discover -s scripts/tests"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os_lifecycle as ol  # noqa: E402

D = dt.date.fromisoformat

LIFECYCLE = """\
[fedora-41]
product = "fedora"
cycle = "41"
eol = 2025-12-15
os_label = "os-fedora-41"

[fedora-44]
product = "fedora"
cycle = "44"
eol = 2027-06-02  # cached
os_label = "os-fedora-44"

["opensuse-16.0"]
product = "opensuse"
cycle = "16.0"
eol = 2027-10-31
os_label = "os-opensuse-leap-16.0"

[rocky-10]
product = "rocky-linux"
cycle = "10"
eol = 2035-05-31
os_label = "os-rocky-10"
minor_releases = true
latest_minor = "10.3"
latest_minor_date = 2026-11-16

[debian-forky]
product = "debian"
cycle = "testing"
eol = "none"
os_label = "os-debian-forky"
codename = "forky"

[tools-tree]
product = "fedora"
cycle = "44"
eol = 2027-06-02
"""

README = """\
# Title

| OS | Status | [EPYC 7003][cert-3.0] | [EPYC 9004][cert-3.1] |
|---|---|---|---|
| Debian Forky | ✅ | [c3.0.0-1](https://x/issues/1) |
| Fedora 41 | ✅ | [c3.0.0-1](https://x/issues/2) |
| Fedora 44 | ✅ | [c3.0.0-1](https://x/issues/3) |
| Opensuse leap 16.0 | ✅ | [c3.0.0-0](https://x/issues/4) |
| Rocky 10.1 | ✅ | [c3.0.0-0](https://x/issues/5) |
| Rocky 10.3 | ✅ | [c3.0.0-1](https://x/issues/6) |

✅ Latest Level Certified

<!-- os-lifecycle:upcoming-removals:start -->
<!-- os-lifecycle:upcoming-removals:end -->

See more.
"""

CERTS = """\
# Certification Levels by Hardware

AMD EPYC 7003 (Milan)
-------------
| OS |  Status |  3.0 Certification |
|---|---|---|
| Fedora 41 | ✅ | [c3.0.0-1](https://x/issues/2) |
| Fedora 44 | ✅ | [c3.0.0-1](https://x/issues/3) |
| Rocky 10.0 |  ❌ |  N/A |

AMD EPYC 9004 (Genoa)
-------------
| OS |  Status |  3.0 Certification | 3.1 Certification |
|---|---|---|---|
| Fedora 41 | ✅ | [c3.0.0-1](https://x/issues/7) |

# Certification Level Definitions

| Level | Features Certified |
|---|---|
| 3.0.0-0 | SEV-SNP Attestation |

# End-of-Life OS Certifications (Archived)

| OS | EOL date | Processor | Last certified | Last release with images | Archived |
|---|---|---|---|---|---|
"""

TODAY = D("2026-11-24")


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for key in ("fedora-41", "fedora-44", "opensuse-16.0", "rocky-10", "debian-forky"):
            for kind in ("host", "guest"):
                d = self.root / "images" / f"{kind}-{key}"
                d.mkdir(parents=True)
                (d / "mkosi.conf").write_text("[Distribution]\n")
        self.write(ol.LIFECYCLE_FILE, LIFECYCLE)
        self.write(ol.README_FILE, README)
        self.write(ol.CERTS_FILE, CERTS)
        self.write(ol.TOOLS_TREE_CONF,
                   "[Build]\nToolsTreeDistribution=fedora\nToolsTreeRelease=44\n")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def read(self, rel):
        return (self.root / rel).read_text()

    @property
    def entries(self):
        return ol.load_entries(self.root)


class StageTests(RepoTestCase):
    def test_stage_boundaries(self):
        e = self.entries["fedora-44"]  # EOL 2027-06-02
        self.assertEqual(e.stage(D("2027-03-03")), ol.SUPPORTED)
        self.assertEqual(e.stage(D("2027-03-04")), ol.APPROACHING)
        self.assertEqual(e.stage(D("2027-06-02")), ol.DEPRECATED)
        self.assertEqual(e.stage(D("2027-07-02")), ol.DEPRECATED)
        self.assertEqual(e.stage(D("2027-07-03")), ol.OVERDUE)

    def test_extension_moves_deadline(self):
        e = self.entries["fedora-41"]
        e.extended_until = D("2026-02-28")
        self.assertEqual(e.stage(D("2026-02-01")), ol.DEPRECATED)

    def test_testing_release_never_eol(self):
        self.assertEqual(self.entries["debian-forky"].stage(D("2099-01-01")), ol.SUPPORTED)

    def test_tools_tree_deadline_is_eol(self):
        e = self.entries["tools-tree"]
        self.assertEqual(e.removal_deadline, e.eol)
        self.assertEqual(e.stage(D("2027-06-03")), ol.OVERDUE)

    def test_matrix_name_matches_certification_workflow(self):
        self.assertEqual(ol.matrix_name("os-opensuse-leap-16.0"), "Opensuse leap 16.0")
        self.assertEqual(ol.matrix_name("os-debian-forky"), "Debian forky")


class LabelStatusTests(RepoTestCase):
    def status(self, label, today=TODAY):
        return ol.label_status(self.entries, label, today)

    def test_eol_label_not_counted(self):
        s = self.status("os-fedora-41")
        self.assertFalse(s["counted"])
        self.assertEqual(s["stage"], ol.OVERDUE)
        self.assertIn("2025-12-15", s["reason"])

    def test_supported_and_untracked_labels_counted(self):
        self.assertTrue(self.status("os-fedora-44")["counted"])
        self.assertTrue(self.status("os-opensuse-leap-16.0")["counted"])
        self.assertEqual(self.status("os-ubuntu-24.04")["stage"], ol.UNTRACKED)
        self.assertTrue(self.status("os-ubuntu-24.04")["counted"])
        self.assertTrue(self.status("")["counted"])

    def test_superseded_rocky_minor(self):
        self.assertEqual(self.status("os-rocky-10.2")["stage"], ol.SUPERSEDED)
        self.assertFalse(self.status("os-rocky-10.2")["counted"])
        self.assertTrue(self.status("os-rocky-10.3")["counted"])
        self.assertTrue(self.status("os-rocky-10.10")["counted"])  # numeric, not string, compare

    def test_label_prefix_does_not_match_other_release(self):
        self.assertEqual(self.status("os-fedora-411")["stage"], ol.UNTRACKED)


class GuardAndMatrixTests(RepoTestCase):
    def test_guard_warns_by_default_and_fails_strict(self):
        entries = self.entries
        self.assertEqual(ol.guard(entries, ["host-fedora-41"], False, TODAY), 0)
        self.assertEqual(ol.guard(entries, ["host-fedora-41"], True, TODAY), 1)
        self.assertEqual(ol.guard(entries, ["guest-fedora-44"], True, TODAY), 0)
        self.assertEqual(ol.guard(entries, ["host-unknown-1"], True, TODAY), 1)

    def test_strict_guard_fails_on_eol_tools_tree(self):
        self.assertEqual(ol.guard(self.entries, ["fedora-44"], True, D("2027-06-03")), 1)

    def test_matrix_excludes_removed_and_tools_tree(self):
        self.write(ol.LIFECYCLE_FILE, ol.set_toml_fields(
            LIFECYCLE, "fedora-41", {"state": "removed", "removed": TODAY}))
        include = ol.build_matrix(self.entries)["include"]
        self.assertIn({"distro": "opensuse", "release": "16.0"}, include)
        self.assertIn({"distro": "debian", "release": "forky"}, include)
        self.assertNotIn({"distro": "fedora", "release": "41"}, include)
        self.assertEqual(len(include), 4)


class ValidateTests(RepoTestCase):
    def test_repo_fixture_is_valid(self):
        self.assertEqual(ol.validate(self.root, self.entries, TODAY), [])

    def test_untracked_and_missing_images(self):
        (self.root / "images" / "host-ubuntu-27.04").mkdir()
        for p in (self.root / "images" / "guest-fedora-44").iterdir():
            p.unlink()
        (self.root / "images" / "guest-fedora-44").rmdir()
        errors = "\n".join(ol.validate(self.root, self.entries, TODAY))
        self.assertIn("ubuntu-27.04", errors)
        self.assertIn("images/guest-fedora-44", errors)

    def test_removed_entry_with_images(self):
        self.write(ol.LIFECYCLE_FILE, ol.set_toml_fields(
            LIFECYCLE, "fedora-41", {"state": "removed", "removed": TODAY}))
        self.assertIn("still exist", "\n".join(ol.validate(self.root, self.entries, TODAY)))

    def test_tools_tree_mismatch(self):
        self.write(ol.TOOLS_TREE_CONF, "ToolsTreeDistribution=fedora\nToolsTreeRelease=45\n")
        self.assertIn("uses fedora 45", "\n".join(ol.validate(self.root, self.entries, TODAY)))

    def test_extension_cap(self):
        self.write(ol.LIFECYCLE_FILE, ol.set_toml_fields(
            LIFECYCLE, "fedora-41", {"extended_until": D("2026-06-01")}))
        self.assertIn("capped", "\n".join(ol.validate(self.root, self.entries, TODAY)))

    def test_onboarding_rule_applies_to_new_entries_only(self):
        base = ol.parse_entries(LIFECYCLE)
        self.assertEqual(ol.validate(self.root, self.entries, TODAY, base), [])
        del base["fedora-44"]  # fedora-44 is "new" in this PR; 190 days to EOL is enough
        self.assertEqual(ol.validate(self.root, self.entries, TODAY, base), [])
        errors = ol.validate(self.root, self.entries, D("2027-01-01"), base)
        self.assertTrue(any("at least 180 days" in e for e in errors), errors)

    def test_unknown_field_rejected(self):
        with self.assertRaises(ol.LifecycleError):
            ol.parse_entries('[x-1]\nproduct = "x"\ncycle = "1"\neol = "none"\n'
                             'os_label = "os-x-1"\neoll = 2020-01-01\n')


def fake_api(product):
    return {
        "fedora": [
            {"name": "45", "releaseDate": "2026-11-03", "eolFrom": "2027-12-08"},
            {"name": "44", "releaseDate": "2026-04-28", "eolFrom": "2027-06-09"},
            {"name": "43", "releaseDate": "2025-10-28", "eolFrom": "2026-12-01"},
            {"name": "41", "releaseDate": "2024-10-29", "eolFrom": "2025-12-15"},
        ],
        "opensuse": [{"name": "16.0", "releaseDate": "2025-10-01", "eolFrom": "2027-10-31"}],
        "rocky-linux": [{"name": "10", "releaseDate": "2025-06-11", "eolFrom": "2035-05-31",
                         "latest": {"name": "10.4", "date": "2027-05-20"}}],
        "debian": [{"name": "14", "codename": "Forky", "releaseDate": "2027-06-12",
                    "eolFrom": "2032-06-30"}],
    }[product]


class CheckTests(RepoTestCase):
    def test_detects_changes_events_and_candidates(self):
        result = ol.check(self.entries, TODAY, fake_api)
        changed = {(c["key"], c["field"]): c["new"] for c in result["changes"]}
        self.assertEqual(changed[("fedora-44", "eol")], D("2027-06-09"))
        self.assertEqual(changed[("tools-tree", "eol")], D("2027-06-09"))
        self.assertEqual(changed[("rocky-10", "latest_minor")], "10.4")
        self.assertNotIn(("fedora-41", "eol"), changed)
        self.assertTrue(any("released as debian 14" in e for e in result["events"]))
        # Fedora 45 is newer than everything tracked; 43 is older and too close to EOL
        self.assertEqual([(c["product"], c["cycle"]) for c in result["candidates"]],
                         [("fedora", "45")])
        self.assertEqual(result["problems"], [])

    def test_apply_changes_keeps_comments(self):
        result = ol.check(self.entries, TODAY, fake_api)
        text = ol.apply_changes(LIFECYCLE, result["changes"])
        self.assertIn("eol = 2027-06-09  # cached", text)
        self.assertIn('latest_minor = "10.4"', text)
        self.assertIn("latest_minor_date = 2027-05-20", text)
        self.assertEqual(ol.parse_entries(text)["fedora-41"].eol, D("2025-12-15"))

    def test_fetch_failure_is_reported(self):
        def broken(product):
            raise OSError("offline")
        result = ol.check(self.entries, TODAY, broken)
        self.assertTrue(result["problems"])

    def test_report_tracking_issues(self):
        entries = self.entries
        rep = ol.report(self.root, entries, TODAY, ol.check(entries, TODAY, fake_api))
        by_key = {e["key"]: e for e in rep["entries"]}
        self.assertEqual(by_key["fedora-41"]["issue_title"], "EOL: Fedora 41 (2025-12-15)")
        self.assertIn("key=fedora-41 stage=overdue", by_key["fedora-41"]["issue_body"])
        self.assertIn("fedora 45", by_key["fedora-41"]["issue_body"])
        self.assertIsNone(by_key["fedora-44"]["issue_title"])
        self.assertEqual(by_key["rocky-10"]["label_regex"], r"^os\-rocky\-10(\.\d+)*$")
        json.dumps(rep, default=ol._json_default)  # serialisable for the workflow


class DocsTests(RepoTestCase):
    def test_sync_readme_marks_eol_rows_and_lists_removals(self):
        text = ol.sync_readme(README, self.entries, TODAY)
        self.assertIn("| Fedora 41 | 🔚 |", text)
        self.assertIn("| Fedora 44 | ✅ |", text)
        self.assertIn("| Rocky 10.1 | ✅ |", text)  # superseded < 90 days ago
        self.assertIn("**Fedora 41** reached end-of-life on 2025-12-15", text)
        self.assertEqual(ol.sync_readme(text, self.entries, TODAY), text)  # idempotent

    def test_sync_readme_deprecates_superseded_minor_after_90_days(self):
        text = ol.sync_readme(README, self.entries, D("2027-02-14"))
        self.assertIn("| Rocky 10.1 | 🔚 |", text)
        self.assertIn("| Rocky 10.3 | ✅ |", text)

    def test_sync_readme_clears_block(self):
        text = ol.sync_readme(README, self.entries, TODAY)
        later = ol.parse_entries(ol.set_toml_fields(
            LIFECYCLE, "fedora-41", {"state": "removed", "removed": TODAY}))
        self.assertNotIn("Upcoming removals", ol.sync_readme(text, later, TODAY))

    def test_archive_rows(self):
        moved = ol.archive_rows(self.root, ol.row_pattern("Rocky 10.0"), D("2025-11-25"),
                                "v3.0.0-1", TODAY)
        self.assertEqual(moved, 1)
        self.assertIn("| Rocky 10.0 | 2025-11-25 | EPYC 7003 | N/A | v3.0.0-1 | 2026-11-24 |",
                      self.read(ol.CERTS_FILE))

    def test_drop(self):
        followups = ol.drop(self.root, "fedora-41", "v3.0.0-1", TODAY)
        self.assertFalse((self.root / "images" / "host-fedora-41").exists())
        self.assertFalse((self.root / "images" / "guest-fedora-41").exists())
        entry = self.entries["fedora-41"]
        self.assertEqual((entry.state, entry.removed, entry.last_release),
                         ("removed", TODAY, "v3.0.0-1"))
        self.assertNotIn("Fedora 41", self.read(ol.README_FILE).split("✅ Latest")[0])
        certs = self.read(ol.CERTS_FILE)
        live, archive = certs.split(ol.ARCHIVE_HEADING)
        self.assertNotIn("Fedora 41", live)
        self.assertIn("| Fedora 41 | 2025-12-15 | EPYC 7003 | [c3.0.0-1](https://x/issues/2) |",
                      archive)
        self.assertIn("| Fedora 41 | 2025-12-15 | EPYC 9004 |", archive)
        # Archived rows must stay invisible to update-certification-matrix.yml's table search
        self.assertFalse(any(l.startswith("AMD EPYC") for l in archive.splitlines()))
        self.assertTrue(any("Suggested PR title" in f for f in followups))
        self.assertEqual(ol.validate(self.root, self.entries, TODAY), [])
        with self.assertRaises(ol.LifecycleError):
            ol.drop(self.root, "fedora-41", None, TODAY)

    def test_release_notes(self):
        self.write(ol.LIFECYCLE_FILE, ol.set_toml_fields(
            LIFECYCLE, "fedora-41",
            {"state": "removed", "removed": TODAY, "last_release": "v3.0.0-1"}))
        notes = ol.release_notes(self.entries, "v3.1.0", D("2026-11-04"), D("2027-04-01"))
        self.assertIn("### Removed (end-of-life)", notes)
        self.assertIn("Fedora 41: upstream EOL 2025-12-15", notes)
        self.assertIn("### Approaching end-of-life", notes)
        self.assertIn("Fedora 44: upstream EOL 2027-06-02", notes)
        later = ol.release_notes(self.entries, "v3.2.0", D("2026-12-01"), TODAY)
        self.assertNotIn("Fedora 41", later)


class RepositoryTests(unittest.TestCase):
    def test_repository_lifecycle_file_is_valid(self):
        entries = ol.load_entries(ol.REPO_ROOT)
        self.assertEqual(ol.validate(ol.REPO_ROOT, entries, TODAY), [])


if __name__ == "__main__":
    unittest.main()
