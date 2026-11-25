#!/usr/bin/env python3
"""Track end-of-life (EOL) dates for sev-certify OS images.

Implements the tooling side of docs/os-lifecycle-policy.md. The source of
truth is images/os-lifecycle.toml; upstream dates come from endoflife.date.

Subcommands:
    status         print the lifecycle stage of every tracked entry
    check          compare cached EOL dates with endoflife.date (weekly CI)
    guard          warn about (or, with --strict, fail on) EOL images
    label-status   decide whether a certificate for an os-* label is counted
    matrix         print the build-and-release workflow matrix as JSON
    validate       check os-lifecycle.toml against images/ and the tools tree
    sync-docs      mark EOL rows in README.md and list upcoming removals
    release-notes  generate notes for a tagged release
    archive        move certification matrix rows to the EOL archive table
    drop           remove an EOL OS from the repository (removal checklist)

Requires Python 3.11+ and only the standard library.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("os_lifecycle.py requires Python 3.11+ (tomllib)")

REPO_ROOT = Path(__file__).resolve().parent.parent
LIFECYCLE_FILE = "images/os-lifecycle.toml"
README_FILE = "README.md"
CERTS_FILE = "docs/certifications.md"
POLICY_FILE = "docs/os-lifecycle-policy.md"
TOOLS_TREE_CONF = "modules/build/common/mkosi.conf"
TOOLS_TREE_KEY = "tools-tree"
API_URL = "https://endoflife.date/api/v1/products/{product}/"

WARN_DAYS = 90             # Approaching EOL starts at T - 90 days
GRACE_DAYS = 30            # removal deadline is T + 30 days
MAX_EXTENSION_DAYS = 90    # extensions are capped at T + 90 days
ONBOARDING_MIN_DAYS = 180  # new releases need 6 months to EOL

SUPPORTED = "supported"
APPROACHING = "approaching"
DEPRECATED = "deprecated"
OVERDUE = "overdue"
REMOVED = "removed"
SUPERSEDED = "superseded"
UNTRACKED = "untracked"
STAGE_TITLES = {
    SUPPORTED: "Supported",
    APPROACHING: "Approaching EOL",
    DEPRECATED: "Deprecated",
    OVERDUE: "Deprecated (removal overdue)",
    REMOVED: "Removed",
    SUPERSEDED: "Superseded minor release",
    UNTRACKED: "Not tracked",
}
# Stages whose new certifications are added to the certification matrix
COUNTED_STAGES = {SUPPORTED, APPROACHING, UNTRACKED}
EOL_STAGES = {DEPRECATED, OVERDUE}

EOL_LABEL = "os-eol"
EOL_MARK = "🔚"
UPCOMING_START = "<!-- os-lifecycle:upcoming-removals:start -->"
UPCOMING_END = "<!-- os-lifecycle:upcoming-removals:end -->"
ARCHIVE_HEADING = "# End-of-Life OS Certifications (Archived)"
ARCHIVE_ANCHOR = "end-of-life-os-certifications-archived"

ENTRY_FIELDS = {
    "product", "cycle", "eol", "os_label", "minor_releases", "latest_minor",
    "latest_minor_date", "codename", "extended_until", "state", "removed",
    "last_release",
}


class LifecycleError(Exception):
    pass


def matrix_name(os_label: str) -> str:
    """Matrix row name for an OS label, as update-certification-matrix.yml derives it."""
    _, os_id, *version = os_label.split("-")
    name = os_id[:1].upper() + os_id[1:]
    return f"{name} {' '.join(version)}" if version else name


def row_pattern(name: str, any_minor: bool = False) -> re.Pattern:
    """Matches certification matrix rows named `name`, like update-certification-matrix.yml."""
    words = r"\s+".join(re.escape(w) for w in name.split())
    suffix = r"(\.\d+)*" if any_minor else ""
    return re.compile(rf"^\|\s*{words}{suffix}\s*\|", re.IGNORECASE)


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version))


def _date(value) -> dt.date | None:
    if value is None or value is False:
        return None
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def repo_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{os.environ.get('GITHUB_REPOSITORY', 'AMDEPYC/sev-certify')}"


@dataclasses.dataclass
class Entry:
    key: str
    product: str
    cycle: str
    eol: dt.date | None
    os_label: str | None = None
    minor_releases: bool = False
    latest_minor: str | None = None
    latest_minor_date: dt.date | None = None
    codename: str | None = None
    extended_until: dt.date | None = None
    state: str | None = None
    removed: dt.date | None = None
    last_release: str | None = None

    @property
    def is_tools_tree(self) -> bool:
        return self.key == TOOLS_TREE_KEY

    @property
    def name(self) -> str:
        if self.is_tools_tree:
            return f"Build tools tree ({self.product.capitalize()} {self.cycle})"
        return matrix_name(self.os_label)

    @property
    def distro(self) -> str:
        return self.key.split("-", 1)[0]

    @property
    def release(self) -> str:
        return self.key.split("-", 1)[1]

    @property
    def image_dirs(self) -> list[str]:
        if self.is_tools_tree:
            return []
        return [f"images/host-{self.key}", f"images/guest-{self.key}"]

    @property
    def warn_from(self) -> dt.date | None:
        return self.eol - dt.timedelta(days=WARN_DAYS) if self.eol else None

    @property
    def removal_deadline(self) -> dt.date | None:
        if not self.eol:
            return None
        if self.is_tools_tree:
            return self.eol  # the tools tree must be bumped before its EOL
        return self.extended_until or self.eol + dt.timedelta(days=GRACE_DAYS)

    def stage(self, today: dt.date) -> str:
        if self.state == REMOVED:
            return REMOVED
        if self.eol is None or today < self.warn_from:
            return SUPPORTED
        if today < self.eol:
            return APPROACHING
        if today <= self.removal_deadline:
            return DEPRECATED
        return OVERDUE

    @property
    def label_regex(self) -> str:
        suffix = r"(\.\d+)*" if self.minor_releases else ""
        return f"^{re.escape(self.os_label)}{suffix}$"

    @property
    def row_regex(self) -> re.Pattern:
        """Matches certification matrix rows for this entry (any minor if minor_releases)."""
        return row_pattern(self.name, self.minor_releases)

    def minor_superseded_from(self, version: str) -> dt.date | None:
        """Date a minor release was superseded, or None if it is the latest."""
        minor = version_tuple(version)
        if (self.minor_releases and self.latest_minor and self.latest_minor_date
                and len(minor) > len(version_tuple(self.cycle))
                and minor < version_tuple(self.latest_minor)):
            return self.latest_minor_date
        return None


# --------------------------------------------------------------------------
# os-lifecycle.toml
# --------------------------------------------------------------------------

def parse_entries(text: str) -> dict[str, Entry]:
    entries = {}
    for key, raw in tomllib.loads(text).items():
        unknown = set(raw) - ENTRY_FIELDS
        if unknown:
            raise LifecycleError(f"[{key}]: unknown field(s) {', '.join(sorted(unknown))}")
        for required in ("product", "cycle", "eol"):
            if required not in raw:
                raise LifecycleError(f"[{key}]: missing required field '{required}'")
        eol = raw["eol"]
        if eol == "none":
            eol = None
        elif not isinstance(eol, dt.date):
            raise LifecycleError(f'[{key}]: eol must be a date (YYYY-MM-DD) or "none"')
        if key != TOOLS_TREE_KEY and not str(raw.get("os_label", "")).startswith("os-"):
            raise LifecycleError(f"[{key}]: os_label must start with 'os-'")
        if raw.get("state") not in (None, REMOVED):
            raise LifecycleError(f'[{key}]: state must be "{REMOVED}" if set')
        entries[key] = Entry(
            key=key,
            product=raw["product"],
            cycle=str(raw["cycle"]),
            eol=eol,
            os_label=raw.get("os_label"),
            minor_releases=bool(raw.get("minor_releases", False)),
            latest_minor=raw.get("latest_minor"),
            latest_minor_date=_date(raw.get("latest_minor_date")),
            codename=raw.get("codename"),
            extended_until=_date(raw.get("extended_until")),
            state=raw.get("state"),
            removed=_date(raw.get("removed")),
            last_release=raw.get("last_release"),
        )
    return entries


def load_entries(root: Path) -> dict[str, Entry]:
    return parse_entries((root / LIFECYCLE_FILE).read_text())


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.date):
        return value.isoformat()
    return json.dumps(value)


def set_toml_fields(text: str, section: str, fields: dict) -> str:
    """Set fields in a [section] of a TOML file, keeping its layout and comments."""
    lines = text.splitlines(keepends=True)
    header = re.compile(rf"^\[(\"?){re.escape(section)}\1\]\s*(#.*)?$")
    start = next((i for i, l in enumerate(lines) if header.match(l.strip())), None)
    if start is None:
        raise LifecycleError(f"section [{section}] not found")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
               len(lines))
    for name, value in fields.items():
        pattern = re.compile(rf"^(\s*{re.escape(name)}\s*=\s*)[^#\n]*?(\s*#.*)?$")
        for i in range(start + 1, end):
            m = pattern.match(lines[i].rstrip("\n"))
            if m:
                lines[i] = f"{m.group(1)}{toml_value(value)}{m.group(2) or ''}\n"
                break
        else:
            last = max((i for i in range(start + 1, end) if "=" in lines[i]), default=start)
            lines.insert(last + 1, f"{name} = {toml_value(value)}\n")
            end += 1
    return "".join(lines)


# --------------------------------------------------------------------------
# Repository inspection
# --------------------------------------------------------------------------

def image_keys(root: Path) -> set[str]:
    images = root / "images"
    return {p.name.split("-", 1)[1] for p in images.iterdir()
            if p.is_dir() and re.match(r"^(host|guest)-", p.name)}


def tools_tree_release(root: Path) -> tuple[str | None, str | None]:
    text = (root / TOOLS_TREE_CONF).read_text()
    dist = re.search(r"^ToolsTreeDistribution=(\S+)", text, re.MULTILINE)
    rel = re.search(r"^ToolsTreeRelease=(\S+)", text, re.MULTILINE)
    return (dist.group(1) if dist else None, rel.group(1) if rel else None)


def git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=root, check=True, text=True,
                              capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def latest_release_tag(root: Path) -> str | None:
    out = git(root, "tag", "-l", "v*", "--sort=-v:refname")
    return out.split()[0] if out and out.split() else None


# --------------------------------------------------------------------------
# label-status / guard / matrix / validate
# --------------------------------------------------------------------------

def find_entry_for_label(entries: dict[str, Entry], label: str) -> Entry | None:
    for entry in entries.values():
        if entry.os_label and re.match(entry.label_regex, label, re.IGNORECASE):
            return entry
    return None


def label_status(entries: dict[str, Entry], label: str, today: dt.date) -> dict:
    """Decide whether a new certificate for an OS label goes into the certification matrix."""
    entry = find_entry_for_label(entries, label) if label else None
    result = {"label": label, "key": None, "name": matrix_name(label) if label else "",
              "stage": UNTRACKED, "eol": "", "reason": ""}
    if entry is None:
        result["counted"] = True
        return result
    stage = entry.stage(today)
    version = label.rsplit("-", 1)[-1]
    superseded = entry.minor_superseded_from(version) if label != entry.os_label else None
    if stage in COUNTED_STAGES and superseded:
        stage = SUPERSEDED
    result.update(key=entry.key, stage=stage, eol=str(entry.eol or ""))
    name = result["name"]
    if stage == SUPERSEDED:
        result["reason"] = (f"{name} was superseded by {entry.name.split()[0]} "
                            f"{entry.latest_minor} (GA {entry.latest_minor_date}).")
    elif stage == REMOVED:
        result["reason"] = (f"{name} reached upstream end-of-life on {entry.eol} and was "
                            f"removed from sev-certify on {entry.removed}.")
    elif stage in EOL_STAGES:
        result["reason"] = (f"{name} reached upstream end-of-life on {entry.eol} and is "
                            f"scheduled for removal by {entry.removal_deadline}.")
    result["counted"] = stage in COUNTED_STAGES
    return result


def annotate(level: str, message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} file={LIFECYCLE_FILE}::{message}")
    else:
        print(f"{level}: {message}", file=sys.stderr)


def guard(entries: dict[str, Entry], targets: list[str], strict: bool,
          today: dt.date) -> int:
    """Report EOL images. Returns non-zero in strict mode if any is past EOL."""
    keys = []
    for target in targets:
        key = re.sub(r"^(host|guest)-", "", target)
        if key not in entries:
            annotate("error" if strict else "warning",
                     f"{target} is not tracked in {LIFECYCLE_FILE}")
            if strict:
                return 1
            continue
        keys.append(key)
    if TOOLS_TREE_KEY in entries:
        keys.append(TOOLS_TREE_KEY)

    failed = False
    for key in dict.fromkeys(keys):
        entry = entries[key]
        stage = entry.stage(today)
        if stage == APPROACHING:
            annotate("notice", f"{entry.name} reaches end-of-life on {entry.eol} "
                               f"(removal deadline {entry.removal_deadline}).")
        elif stage in EOL_STAGES or stage == REMOVED:
            msg = f"{entry.name} reached end-of-life on {entry.eol}"
            if entry.is_tools_tree:
                msg += f"; bump ToolsTreeRelease in {TOOLS_TREE_CONF}"
            else:
                msg += (f"; remove it by {entry.removal_deadline} with "
                        f"'python3 scripts/os_lifecycle.py drop {key}'")
            if strict:
                annotate("error", f"{msg}. EOL images must not ship in a tagged release.")
                failed = True
            else:
                annotate("warning", f"{msg}.")
    return 1 if failed else 0


def build_matrix(entries: dict[str, Entry]) -> dict:
    return {"include": [{"distro": e.distro, "release": e.release}
                        for e in entries.values()
                        if not e.is_tools_tree and e.state != REMOVED]}


def validate(root: Path, entries: dict[str, Entry], today: dt.date,
             base_entries: dict[str, Entry] | None = None) -> list[str]:
    errors = []
    keys_on_disk = image_keys(root)
    for entry in entries.values():
        if entry.is_tools_tree:
            continue
        present = [d for d in entry.image_dirs if (root / d).is_dir()]
        if entry.state == REMOVED:
            if present:
                errors.append(f"[{entry.key}]: state is removed but {', '.join(present)} still exist")
            if not entry.removed:
                errors.append(f"[{entry.key}]: removed entries need a 'removed' date")
        elif len(present) != 2:
            missing = sorted(set(entry.image_dirs) - set(present))
            errors.append(f"[{entry.key}]: missing {', '.join(missing)}")
        if entry.extended_until:
            if not entry.eol:
                errors.append(f"[{entry.key}]: extended_until requires an eol date")
            elif entry.extended_until > entry.eol + dt.timedelta(days=MAX_EXTENSION_DAYS):
                errors.append(f"[{entry.key}]: extended_until is capped at eol + "
                              f"{MAX_EXTENSION_DAYS} days ({entry.eol + dt.timedelta(days=MAX_EXTENSION_DAYS)})")
        if entry.minor_releases and bool(entry.latest_minor) != bool(entry.latest_minor_date):
            errors.append(f"[{entry.key}]: set both latest_minor and latest_minor_date")
    for key in sorted(keys_on_disk - set(entries)):
        errors.append(f"images/*-{key} has no [{key}] entry in {LIFECYCLE_FILE}")

    tools = entries.get(TOOLS_TREE_KEY)
    dist, rel = tools_tree_release(root)
    if tools is None:
        errors.append(f"[{TOOLS_TREE_KEY}] entry is missing")
    elif (dist, rel) != (tools.product, tools.cycle):
        errors.append(f"[{TOOLS_TREE_KEY}] says {tools.product} {tools.cycle} but "
                      f"{TOOLS_TREE_CONF} uses {dist} {rel}")

    if base_entries is not None:
        # Onboarding rule: new releases need ONBOARDING_MIN_DAYS to EOL
        for key, entry in entries.items():
            if key in base_entries or entry.state == REMOVED or entry.eol is None:
                continue
            remaining = (entry.eol - today).days
            if remaining < ONBOARDING_MIN_DAYS:
                errors.append(f"[{key}]: new OS releases need at least {ONBOARDING_MIN_DAYS} "
                              f"days to EOL; {entry.name} has {remaining} (EOL {entry.eol})")
    return errors


# --------------------------------------------------------------------------
# check (endoflife.date)
# --------------------------------------------------------------------------

def fetch_product(product: str) -> list[dict]:
    req = urllib.request.Request(API_URL.format(product=product),
                                 headers={"User-Agent": "sev-certify-os-lifecycle"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["result"]["releases"]


def check(entries: dict[str, Entry], today: dt.date,
          fetch: Callable[[str], list[dict]] = fetch_product) -> dict:
    """Compare cached data with endoflife.date. Returns changes, events and candidates."""
    releases: dict[str, list[dict]] = {}
    changes, problems, events, candidates = [], [], [], []

    for entry in entries.values():
        if entry.state == REMOVED:
            continue
        if entry.product not in releases:
            try:
                releases[entry.product] = fetch(entry.product)
            except Exception as exc:
                problems.append(f"{entry.key}: could not fetch '{entry.product}' "
                                f"from endoflife.date: {exc}")
                continue
        rels = releases[entry.product]
        if entry.eol is None:
            if entry.codename:
                rel = next((r for r in rels
                            if (r.get("codename") or "").lower() == entry.codename.lower()), None)
                if rel:
                    events.append(f"{entry.name} was released as {entry.product} {rel['name']}: "
                                  f"rename `{entry.key}` (policy section 3).")
            continue
        rel = next((r for r in rels if str(r.get("name")) == entry.cycle), None)
        if rel is None:
            problems.append(f"{entry.key}: release '{entry.cycle}' not found for "
                            f"'{entry.product}' on endoflife.date")
            continue
        upstream = _date(rel.get("eolFrom"))
        if upstream and upstream != entry.eol:
            changes.append({"key": entry.key, "field": "eol", "old": entry.eol, "new": upstream})
        latest = rel.get("latest") or {}
        if (entry.minor_releases and latest.get("name") and latest.get("date")
                and latest["name"] != entry.latest_minor):
            changes.append({"key": entry.key, "field": "latest_minor",
                            "old": entry.latest_minor, "new": latest["name"]})
            changes.append({"key": entry.key, "field": "latest_minor_date",
                            "old": entry.latest_minor_date, "new": _date(latest["date"])})

    # Successor candidates: GA releases newer than anything tracked, with enough support left
    tracked: dict[str, set[str]] = {}
    for entry in entries.values():
        tracked.setdefault(entry.product, set()).add(entry.cycle)
    for product, cycles in tracked.items():
        rels = releases.get(product) or []
        dates = [_date(r.get("releaseDate")) for r in rels if str(r.get("name")) in cycles]
        newest = max((d for d in dates if d), default=None)
        for r in rels:
            released, eol = _date(r.get("releaseDate")), _date(r.get("eolFrom"))
            if str(r.get("name")) in cycles or not released or released > today:
                continue
            if newest and released <= newest:
                continue
            if eol and (eol - today).days < ONBOARDING_MIN_DAYS:
                continue
            candidates.append({"product": product, "cycle": str(r["name"]),
                               "release_date": released, "eol": eol})
    return {"changes": changes, "problems": problems, "events": events,
            "candidates": candidates}


def apply_changes(text: str, changes: list[dict]) -> str:
    by_key: dict[str, dict] = {}
    for change in changes:
        by_key.setdefault(change["key"], {})[change["field"]] = change["new"]
    for key, fields in by_key.items():
        text = set_toml_fields(text, key, fields)
    return text


def _issue_body(entry: Entry, today: dt.date, candidates: list[dict]) -> str:
    stage = entry.stage(today)
    rows = [
        ("Stage", STAGE_TITLES[stage]),
        ("EOL date (T)", entry.eol),
        ("Approaching EOL from (T − 90 days)", entry.warn_from),
        ("Removal deadline", entry.removal_deadline),
    ]
    if entry.extended_until:
        rows.append(("Extension", f"until {entry.extended_until}"))
    if entry.is_tools_tree:
        rows.append(("Config", f"`ToolsTreeRelease` in `{TOOLS_TREE_CONF}`"))
    else:
        rows.append(("Images", ", ".join(f"`{d}`" for d in entry.image_dirs)))
        rows.append(("OS label", f"`{entry.os_label}`" + (".*" if entry.minor_releases else "")))
    lines = [
        f"<!-- os-lifecycle key={entry.key} stage={stage} -->",
        f"**{entry.name}** {'reaches' if stage == APPROACHING else 'reached'} upstream "
        f"end-of-life on **{entry.eol}**.",
        "",
        "| | |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in rows),
        "",
        "### Successor candidates",
        "",
    ]
    succ = [c for c in candidates if c["product"] == entry.product]
    lines += [f"- {c['product']} {c['cycle']}: released {c['release_date']}, EOL {c['eol'] or 'n/a'}"
              for c in succ] or ["None on endoflife.date yet that meet the onboarding rule."]
    lines += ["", "### Next steps", ""]
    if entry.is_tools_tree:
        lines += [
            f"- [ ] Bump `ToolsTreeRelease` in `{TOOLS_TREE_CONF}` and update "
            f"`[{TOOLS_TREE_KEY}]` in `{LIFECYCLE_FILE}` before {entry.eol}.",
        ]
    else:
        if stage == APPROACHING:
            lines += [
                f"- [ ] Onboard a successor before {entry.eol} (successor rule).",
                "- [ ] Announce the phase-out to stakeholders (policy section 7).",
                f"- [ ] At {entry.eol}: tagged builds start failing and the weekly check PR "
                f"marks the README row {EOL_MARK}.",
            ]
        lines += [
            f"- [ ] Open the removal PR by {entry.removal_deadline}: "
            f"`python3 scripts/os_lifecycle.py drop {entry.key}`.",
        ]
    lines += ["", f"Maintained by `.github/workflows/os-eol-check.yml`. "
                  f"See the [OS lifecycle policy]({repo_url()}/blob/main/{POLICY_FILE})."]
    return "\n".join(lines)


def _stage_comment(entry: Entry, today: dt.date) -> str:
    stage = entry.stage(today)
    if stage == APPROACHING:
        return (f"**{entry.name}** is now within {WARN_DAYS} days of end-of-life "
                f"({entry.eol}). Start onboarding its successor.")
    if stage == DEPRECATED:
        return (f"**{entry.name}** reached end-of-life on {entry.eol} and is now Deprecated. "
                f"Tagged builds fail until it is removed. Removal deadline: "
                f"{entry.removal_deadline}.")
    if stage == OVERDUE:
        what = (f"`ToolsTreeRelease` in `{TOOLS_TREE_CONF}` still points to it" if entry.is_tools_tree
                else f"{', '.join(f'`{d}`' for d in entry.image_dirs)} still exist")
        return (f"⚠️ The removal deadline for **{entry.name}** ({entry.removal_deadline}) has "
                f"passed and {what}. Remove it now"
                + ("." if entry.is_tools_tree else
                   f": `python3 scripts/os_lifecycle.py drop {entry.key}`."))
    if stage == REMOVED:
        return f"**{entry.name}** was removed on {entry.removed}. Closing."
    return (f"The upstream EOL date for **{entry.name}** is now {entry.eol}, which is outside "
            f"the {WARN_DAYS}-day window. Closing until it approaches again.")


def report(root: Path, entries: dict[str, Entry], today: dt.date, result: dict) -> dict:
    out_entries = []
    for entry in entries.values():
        stage = entry.stage(today)
        item = {
            "key": entry.key,
            "name": entry.name,
            "stage": stage,
            "eol": entry.eol,
            "warn_from": entry.warn_from,
            "removal_deadline": entry.removal_deadline,
            "removed": entry.removed,
            "label_regex": entry.label_regex if entry.os_label else None,
            "images_exist": any((root / d).is_dir() for d in entry.image_dirs),
            "stage_comment": _stage_comment(entry, today),
            "issue_title": None,
            "issue_body": None,
            "archive_comment": None,
        }
        if stage in (APPROACHING, DEPRECATED, OVERDUE):
            item["issue_title"] = f"EOL: {entry.name} ({entry.eol})"
            item["issue_body"] = _issue_body(entry, today, result["candidates"])
        if stage == REMOVED:
            item["archive_comment"] = (
                f"{entry.name} reached upstream end-of-life on {entry.eol} and was removed "
                f"from sev-certify on {entry.removed}. This certification is archived; see "
                f"[docs/certifications.md]({repo_url()}/blob/main/{CERTS_FILE}#{ARCHIVE_ANCHOR}).")
        out_entries.append(item)
    return {"today": today, "entries": out_entries, **result}


def summary_markdown(rep: dict) -> str:
    lines = [f"## OS lifecycle ({rep['today']})", "",
             "| Entry | Stage | EOL | Removal deadline |", "|---|---|---|---|"]
    for e in rep["entries"]:
        lines.append(f"| {e['name']} | {STAGE_TITLES[e['stage']]} | {e['eol'] or 'none'} "
                     f"| {e['removal_deadline'] or '—'} |")
    if rep["changes"]:
        lines += ["", "### Upstream changes", ""]
        lines += [f"- `{c['key']}`: {c['field']} {c['old']} → {c['new']}" for c in rep["changes"]]
    if rep["events"]:
        lines += ["", "### Events", ""] + [f"- {e}" for e in rep["events"]]
    if rep["candidates"]:
        lines += ["", "### New releases eligible for onboarding", ""]
        lines += [f"- {c['product']} {c['cycle']}: released {c['release_date']}, EOL {c['eol']}"
                  for c in rep["candidates"]]
    if rep["problems"]:
        lines += ["", "### Problems", ""] + [f"- ⚠️ {p}" for p in rep["problems"]]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# README.md / docs/certifications.md
# --------------------------------------------------------------------------

def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _master_table(lines: list[str]) -> tuple[int, int]:
    """Row range of the README master table."""
    header = next((i for i, l in enumerate(lines) if "| OS |" in l and "Status" in l), None)
    if header is None:
        raise LifecycleError(f"master table not found in {README_FILE}")
    end = header + 2
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return header + 2, end


def _row_eol(entries: dict[str, Entry], row: str, today: dt.date) -> Entry | None:
    """The entry that makes a matrix row EOL today, if any."""
    for entry in entries.values():
        if entry.is_tools_tree or not entry.row_regex.match(row):
            continue
        if entry.stage(today) in EOL_STAGES:
            return entry
        # Superseded minors become Deprecated 90 days after the next minor's GA (6.3)
        superseded = entry.minor_superseded_from(_cells(row)[0])
        if superseded and today >= superseded + dt.timedelta(days=WARN_DAYS):
            return entry
    return None


def sync_readme(text: str, entries: dict[str, Entry], today: dt.date) -> str:
    lines = text.split("\n")
    start, end = _master_table(lines)
    for i in range(start, end):
        if _row_eol(entries, lines[i], today):
            parts = lines[i].split("|")
            if len(parts) > 3:
                parts[2] = f" {EOL_MARK} "
                lines[i] = "|".join(parts)

    if UPCOMING_START not in lines or UPCOMING_END not in lines:
        raise LifecycleError(f"{README_FILE} is missing the {UPCOMING_START} markers")
    block = []
    for entry in entries.values():
        if not entry.is_tools_tree and entry.stage(today) in EOL_STAGES:
            block.append(f"- **{entry.name}** reached end-of-life on {entry.eol} and will be "
                         f"removed by {entry.removal_deadline}.")
    if block:
        block = ["**Upcoming removals** (see the [OS lifecycle policy](./"
                 f"{POLICY_FILE})):", ""] + block
    s, e = lines.index(UPCOMING_START), lines.index(UPCOMING_END)
    lines[s + 1:e] = block
    return "\n".join(lines)


def archive_rows(root: Path, name_regex: re.Pattern, eol: dt.date | None,
                 last_release: str | None, today: dt.date) -> int:
    """Remove matching rows from the README and move certifications.md rows to the archive."""
    readme = root / README_FILE
    lines = readme.read_text().split("\n")
    start, end = _master_table(lines)
    lines = [l for i, l in enumerate(lines) if not (start <= i < end and name_regex.match(l))]
    readme.write_text("\n".join(lines))

    certs = root / CERTS_FILE
    lines = certs.read_text().split("\n")
    kept, archived, proc = [], [], None
    for line in lines:
        m = re.match(r"^AMD EPYC (\d+)", line)
        if m:
            proc = m.group(1)
        elif line.startswith("# "):
            proc = None
        if proc and line.startswith("|") and name_regex.match(line):
            cells = _cells(line)
            certified = ", ".join(c for c in cells[2:] if c) or "N/A"
            archived.append(f"| {cells[0]} | {eol or '—'} | EPYC {proc} | {certified} "
                            f"| {last_release or '—'} | {today} |")
            continue
        kept.append(line)
    if archived:
        if ARCHIVE_HEADING not in kept:
            raise LifecycleError(f"{CERTS_FILE} is missing the '{ARCHIVE_HEADING}' section")
        i = kept.index(ARCHIVE_HEADING)
        header = next(j for j in range(i, len(kept)) if kept[j].startswith("| OS |"))
        insert = header + 2
        while insert < len(kept) and kept[insert].startswith("|"):
            insert += 1
        kept[insert:insert] = archived
    certs.write_text("\n".join(kept))
    return len(archived)


# --------------------------------------------------------------------------
# release-notes / drop
# --------------------------------------------------------------------------

def release_notes(entries: dict[str, Entry], tag: str, since: dt.date | None,
                  today: dt.date) -> str:
    lines = [f"OS images for {tag} certification."]
    removed = [e for e in entries.values() if e.state == REMOVED and e.removed
               and (since is None or e.removed >= since)]
    if removed:
        lines += ["", "### Removed (end-of-life)", ""]
        lines += [f"- {e.name}: upstream EOL {e.eol}, removed {e.removed}. Last release with "
                  f"images: {e.last_release or 'n/a'}. Results are archived in "
                  f"[docs/certifications.md]({repo_url()}/blob/{tag}/{CERTS_FILE}#{ARCHIVE_ANCHOR})."
                  for e in removed]
    upcoming = [e for e in entries.values()
                if not e.is_tools_tree and e.stage(today) == APPROACHING]
    if upcoming:
        lines += ["", "### Approaching end-of-life", ""]
        lines += [f"- {e.name}: upstream EOL {e.eol}. It will not be included in releases "
                  f"tagged after that date." for e in upcoming]
    return "\n".join(lines) + "\n"


def drop(root: Path, key: str, last_release: str | None, today: dt.date) -> list[str]:
    """Remove an OS from the repository. Returns remaining manual follow-ups."""
    entries = load_entries(root)
    entry = entries.get(key)
    if entry is None or entry.is_tools_tree:
        raise LifecycleError(f"no image entry [{key}] in {LIFECYCLE_FILE}")
    if entry.state == REMOVED:
        raise LifecycleError(f"[{key}] is already removed")
    last_release = last_release or latest_release_tag(root)
    in_git = git(root, "rev-parse", "--is-inside-work-tree") is not None

    for d in entry.image_dirs:
        if (root / d).exists():
            if in_git:
                git(root, "rm", "-r", "-q", "--", d)
            if (root / d).exists():  # untracked build output or not a git checkout
                shutil.rmtree(root / d)

    path = root / LIFECYCLE_FILE
    fields = {"state": REMOVED, "removed": today}
    if last_release:
        fields["last_release"] = last_release
    path.write_text(set_toml_fields(path.read_text(), key, fields))
    entries = load_entries(root)

    moved = archive_rows(root, entry.row_regex, entry.eol, last_release, today)
    readme = root / README_FILE
    readme.write_text(sync_readme(readme.read_text(), entries, today))

    followups = [f"Archived {moved} row(s) in {CERTS_FILE}; check the archive table reads well."]
    refs = git(root, "grep", "-n", "-I", "-F",
               "-e", f"host-{key}", "-e", f"guest-{key}", "-e", entry.name, "--",
               ".", f":!{LIFECYCLE_FILE}", f":!{CERTS_FILE}", f":!{POLICY_FILE}")
    if refs:
        followups.append("Update docs and examples that still mention this OS "
                         "(prefer a long-lived release such as an LTS):\n"
                         + "\n".join(f"    {r}" for r in refs.splitlines()))
    if not any(e.distro == entry.distro and e.state != REMOVED and not e.is_tools_tree
               for e in entries.values()):
        followups.append(
            f"This was the last {entry.distro} release. Remove {entry.distro}-specific branches in "
            "modules/build/host/format-qemu-binary/mkosi.postinst, modules/build/common/network-fix/ "
            "and modules/build/common/load-kernel-modules/mkosi.build, the install hints in "
            "sev_verify/README.md and docs/how-to-generate-certs.md, and the distro list in "
            "images/README.md.")
    followups.append(
        f"After merge, os-eol-check.yml labels the {entry.os_label} certificate issues "
        f"'{EOL_LABEL}', closes them and closes the tracking issue.")
    followups.append(
        f"Suggested PR title: chore: drop {entry.name} support (EOL {entry.eol})")
    return followups


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _json_default(value):
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {value!r}")


def _write_github_output(values: dict) -> None:
    lines = "".join(f"{k}={json.dumps(v) if isinstance(v, bool) else str(v).replace(chr(10), ' ')}\n"
                    for k, v in values.items())
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as out:
            out.write(lines)
    else:
        sys.stdout.write(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today(),
                        help="evaluate stages as of this date (YYYY-MM-DD)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print the stage of every entry")

    p = sub.add_parser("check", help="compare cached data with endoflife.date")
    p.add_argument("--update", action="store_true", help=f"write upstream changes to {LIFECYCLE_FILE}")
    p.add_argument("--report", type=Path, help="write a JSON report for os-eol-check.yml")
    p.add_argument("--summary", action="store_true", help="print a markdown summary")

    p = sub.add_parser("guard", help="warn about EOL images (fail with --strict)")
    p.add_argument("images", nargs="*", help="image names or keys (default: --all)")
    p.add_argument("--all", action="store_true", help="check every image entry")
    p.add_argument("--strict", action="store_true", help="exit non-zero for EOL images")

    p = sub.add_parser("label-status", help="is a certificate for this os-* label counted?")
    p.add_argument("label", nargs="?", default="")
    p.add_argument("--github-output", action="store_true", help="write to $GITHUB_OUTPUT")

    sub.add_parser("matrix", help="print the build matrix as JSON")

    p = sub.add_parser("validate", help=f"check {LIFECYCLE_FILE} against the repository")
    p.add_argument("--base-ref", help="git ref to enforce the onboarding rule against")

    sub.add_parser("sync-docs", help=f"mark EOL rows {EOL_MARK} in {README_FILE}")

    p = sub.add_parser("release-notes", help="generate release notes for a tag")
    p.add_argument("--tag", required=True)
    p.add_argument("--since", type=dt.date.fromisoformat,
                   help="only list OSes removed on or after this date (previous tag date)")

    p = sub.add_parser("archive", help="move matrix rows to the EOL archive table")
    p.add_argument("--name", required=True, help='matrix row name, e.g. "Rocky 10.1"')
    p.add_argument("--eol", type=dt.date.fromisoformat, help="EOL (or superseded) date")
    p.add_argument("--last-release", help="last tagged release with images")

    p = sub.add_parser("drop", help="remove an EOL OS from the repository")
    p.add_argument("key", help="entry key, e.g. fedora-41")
    p.add_argument("--last-release", help="last tagged release with images (default: latest v* tag)")

    args = parser.parse_args(argv)
    root, today = args.root, args.today

    try:
        if args.cmd == "drop":
            for item in drop(root, args.key, args.last_release, today):
                print(f"- {item}")
            return 0
        if args.cmd == "archive":
            moved = archive_rows(root, row_pattern(args.name), args.eol, args.last_release, today)
            print(f"Archived {moved} row(s) for {args.name}")
            return 0

        entries = load_entries(root)

        if args.cmd == "status":
            print(f"{'ENTRY':<16} {'STAGE':<30} {'EOL':<11} REMOVAL DEADLINE")
            for e in entries.values():
                print(f"{e.key:<16} {STAGE_TITLES[e.stage(today)]:<30} "
                      f"{str(e.eol or 'none'):<11} {e.removal_deadline or '—'}")
            return 0

        if args.cmd == "check":
            result = check(entries, today)
            if args.update and result["changes"]:
                path = root / LIFECYCLE_FILE
                path.write_text(apply_changes(path.read_text(), result["changes"]))
                entries = load_entries(root)
            rep = report(root, entries, today, result)
            if args.report:
                args.report.write_text(json.dumps(rep, indent=2, default=_json_default))
            if args.summary:
                print(summary_markdown(rep))
            for problem in result["problems"]:
                annotate("warning", problem)
            return 0

        if args.cmd == "guard":
            targets = args.images
            if args.all or not targets:
                targets = [k for k, e in entries.items()
                           if not e.is_tools_tree and e.state != REMOVED]
            return guard(entries, targets, args.strict, today)

        if args.cmd == "label-status":
            status = label_status(entries, args.label, today)
            if args.github_output:
                _write_github_output(status)
            else:
                print(json.dumps(status, indent=2))
            return 0

        if args.cmd == "matrix":
            print(json.dumps(build_matrix(entries)))
            return 0

        if args.cmd == "validate":
            base = None
            if args.base_ref:
                # No lifecycle file on the base ref yet: nothing counts as newly onboarded
                text = git(root, "show", f"{args.base_ref}:{LIFECYCLE_FILE}")
                base = parse_entries(text) if text is not None else None
            errors = validate(root, entries, today, base)
            for error in errors:
                annotate("error", error)
            if not errors:
                print(f"{LIFECYCLE_FILE}: OK ({len(entries)} entries)")
            return 1 if errors else 0

        if args.cmd == "sync-docs":
            readme = root / README_FILE
            text = readme.read_text()
            new = sync_readme(text, entries, today)
            if new != text:
                readme.write_text(new)
                print(f"Updated {README_FILE}")
            return 0

        if args.cmd == "release-notes":
            print(release_notes(entries, args.tag, args.since, today), end="")
            return 0
    except LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
