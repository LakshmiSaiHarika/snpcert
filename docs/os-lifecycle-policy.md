# OS Lifecycle Policy: Phasing Out End-of-Life Operating Systems

| | |
|---|---|
| **Status** | Proposed |
| **Owner** | Certification component maintainers |
| **Applies to** | Every host/guest image under `images/`, the certification matrix (`README.md`, `docs/certifications.md`), the build tools tree, and release assets |
| **Last reviewed** | 2026-11-24 |

## 1. Context

`sev-certify` builds and certifies host/guest images for a growing set of
Linux distributions, but there is no defined process for what happens when one
of them reaches end-of-life (EOL) upstream. OSes have been dropped ad hoc
before: Ubuntu Oracular in `1e9fe09` after its CI builds broke, and Debian
Bookworm in `938c0a0` because its kernel was older than 6.11. Neither drop
updated the matrix or included any announcement.

As of 2026-11-24, without a policy:

- **Fedora 41** (EOL 2025-12-15), **Ubuntu 25.04** (EOL 2026-01-17) and
  **Ubuntu 25.10** (EOL 2026-07-01) are still in the build matrix and still
  show ✅ in the README.
- New certificates for all three were issued on 2026-10-21 (#346, #347, #353,
  #334), 4–11 months after they stopped getting security updates. The
  `v3.0.0-1` tagged release (2026-11-04) also ships their images.
- CI builds for these OSes still succeed, so a build failure doesn't
  reliably signal EOL. When builds do break, they break suddenly, as they did
  for Oracular.
- **Rocky Linux 10.3** went GA on 2026-11-16, so the Rocky 10.0, 10.1, and
  10.2 matrix rows now describe minor releases that no longer get updates.
- The build tools tree is pinned to **Fedora 44** (`ToolsTreeRelease=44` in
  `modules/build/common/mkosi.conf`), which reaches EOL on 2027-06-02. EOL
  affects the build infrastructure as well as the certified OSes.

## 2. Decision summary

1. **Define EOL** as the date upstream stops publishing *free, public*
   security updates for that release (section 3). The canonical source is
   [endoflife.date](https://endoflife.date).
2. **Track each image's EOL date in the repo**, and check it automatically
   every week (section 4).
3. **Move each OS through fixed lifecycle stages** keyed to its EOL date *T*:
   Approaching EOL at *T − 90 days*, Deprecated at *T*, Removed by
   *T + 30 days* or before the next tagged release, whichever comes first
   (section 5).
4. **Archive certification results; never delete them.** Remove EOL OSes from
   the README master table and move their rows to an "End-of-Life (Archived)"
   table in `docs/certifications.md`. Label their issues `os-eol` (section 6).
5. **Announce at each stage**: tracking issue at T − 90, README notice at T,
   and release notes for the tag that drops the OS (section 7).
6. **Follow a fixed removal checklist** so every drop is complete and
   consistent (section 8).

## 3. Definition of EOL

| Term | Definition |
|---|---|
| **EOL date (*T*)** | The first day the upstream project no longer publishes free, public security updates for the release. This is the `eolFrom` field of the endoflife.date API. |
| **Excluded** | Paid or subscription extended support: Ubuntu Pro/ESM, Debian ELTS, RHEL ELS, and similar. Certification targets the community-supported OS that a user gets from the image. |
| **Superseded minor release** | For distros where only the latest minor release gets updates (Rocky Linux), a minor release is treated as EOL when the next minor release reaches GA. |

### Distro-specific rules

| Distro | endoflife.date product | Notes |
|---|---|---|
| Fedora | `fedora` | About 13 months of support; EOL is 4 weeks after release N+2. Expect one drop and one add per ~6 months. |
| Ubuntu | `ubuntu` | Interim releases get 9 months, LTS releases 5 years. Use `eolFrom` (standard support), not `eoesFrom` (ESM). |
| Debian (stable) | `debian` | `eolFrom` is the end of Debian LTS, which is free and public. `eoasFrom`, when the Debian security team stops, is the **T − 90 trigger at the earliest**; we keep certifying through LTS. |
| Debian testing (`forky`) | n/a | No EOL. When testing is released as stable (forky → Debian 14), **rename** `debian-forky` to `debian-14` and archive the `Debian Forky` row with the note "released as Debian 14". Then decide whether to add the next testing codename. |
| CentOS Stream | `centos-stream` | Treat as a normal release with `eolFrom`. |
| Rocky Linux | `rocky-linux` | The image uses `Release=10`, so it always builds the latest minor and the image needs no change. Matrix rows and `os-rocky-10.x` labels are per minor, and the superseded-minor rule applies to them (section 6.3). |
| openSUSE Leap | `opensuse` | Each minor (16.0, 16.1, …) is pinned in `mkosi.conf` and has its own EOL, so it is handled like a separate release. |

### Onboarding rule (prevents adding OSes that are about to expire)

A new OS release may only be added if it has **at least 6 months to EOL**
when the PR merges, and the PR must add its entry to the lifecycle file
(section 4.1). For example, Fedora 43 (EOL 2026-12-01) shouldn't be added
now, but Fedora 45 (EOL 2027-12-08) should.

### Successor rule (add before remove)

If dropping a release would leave a distro family with no supported image,
the successor release should be onboarded **before** the drop's *T*. This is
the main job of the Approaching EOL stage. The drop itself is still not
blocked: an EOL OS is removed on schedule whether or not its successor is
ready.

## 4. Detecting and tracking EOL dates

### 4.1 Source of truth in the repo

[`images/os-lifecycle.toml`](../images/os-lifecycle.toml) has one entry per
image pair, plus an entry for the tools tree. The full field list is in the
file header. Quote keys that contain a dot (`["ubuntu-26.04"]`).

```toml
# Keys match the image suffix: images/{host,guest}-<key>
[fedora-44]
product  = "fedora"        # endoflife.date product slug
cycle    = "44"            # endoflife.date release name
eol      = 2027-06-02      # cached eolFrom; updated by the weekly check
os_label = "os-fedora-44"  # GitHub label emitted by beacon-report.sh

[rocky-10]
product           = "rocky-linux"
cycle             = "10"
eol               = 2035-05-31
os_label          = "os-rocky-10"
minor_releases    = true          # labels and rows are per minor: os-rocky-10.3
latest_minor      = "10.3"        # cached; older minors are superseded
latest_minor_date = 2026-11-16

[debian-forky]
product  = "debian"
cycle    = "testing"
eol      = "none"          # rolling / pre-release: no EOL; see section 3
os_label = "os-debian-forky"
codename = "forky"         # the weekly check reports when it becomes stable

[tools-tree]               # modules/build/common/mkosi.conf ToolsTreeRelease
product  = "fedora"
cycle    = "44"
eol      = 2027-06-02
```

The matrix row name is derived from `os_label` the same way
`update-certification-matrix.yml` derives it (`os-opensuse-leap-16.0` →
`Opensuse leap 16.0`), so the two can't drift apart.

Caching the date in the repo means builds and the matrix workflow can check
EOL status without network access, and every date change goes through a
reviewed PR.

All tooling is in [`scripts/os_lifecycle.py`](../scripts/os_lifecycle.py)
(Python 3.11+, standard library only). Run `python3 scripts/os_lifecycle.py
status`, or `make lifecycle` in `images/`, to see each entry's stage.

### 4.2 Automated weekly check (`os-eol-check.yml`)

The workflow runs every Monday, on `workflow_dispatch`, and on pushes to
`main` that change `os-lifecycle.toml`. It:

1. Queries `https://endoflife.date/api/v1/products/<product>/` for each entry
   and compares the live `eolFrom` (and, for Rocky, the latest minor) with the
   cached values.
2. Opens or updates a PR on the `os-lifecycle/auto-update` branch that
   updates changed dates in `os-lifecycle.toml`. The same PR also sets the
   README status to 🔚 and updates the "Upcoming removals" note (section 6.1).
3. If an entry is within 90 days of `eol` or past it, opens a tracking issue
   titled `EOL: <name> (<eol>)` with label `os-eol`, or updates the existing
   one. Issues are matched by a hidden `<!-- os-lifecycle key=… stage=… -->`
   marker in the body, so the job is idempotent and survives date changes.
4. Comments on the tracking issue when the stage changes. On the move to
   Deprecated (removal overdue), the comment says the removal deadline has
   passed and gives the `drop` command.
5. Closes the tracking issue when the OS is removed, or when its EOL date
   moves outside the 90-day window.
6. Lists new GA releases of tracked products that meet the onboarding rule
   in the run summary and in the tracking issue of the same product, as
   input for the successor rule. Reports when a tracked testing codename
   becomes a stable release.
7. After a removal PR merges, archives the certificate issues (section 6.2).

PRs that the workflow opens with `GITHUB_TOKEN` don't trigger other workflows.
Close and reopen the PR to run checks on it.

### 4.3 Guards in build and certification paths

| Where | Behaviour for an image whose `eol` is in the past |
|---|---|
| `build-and-release.yml`, **tag** builds (`refs/tags/v*`) | **Fail.** The `lifecycle` job runs `guard --all --strict` before the release is created or any image is built. EOL images must not ship in a new tagged release. The same applies to the tools tree. |
| `build-and-release.yml`, main/PR builds and `images/Makefile` | **Warn** with a `::warning::` annotation or stderr. The build still runs, so the Deprecated window stays usable. |
| `update-certification-matrix.yml` | **Don't add or update a matrix row.** `label-status` classifies the issue's `os-*` label. EOL, removed, and superseded-minor results get the `os-eol` label and a comment explaining that the result was recorded but not counted. Without this guard, running `dispatch --tag v3.0.0-1` (which still contains EOL images) would re-insert archived rows, because the workflow inserts a new row whenever it doesn't find one. Labels not in `os-lifecycle.toml` are counted as before. |
| `lint.yml` (PRs) | **Fail** if `os-lifecycle.toml` doesn't match `images/` (every image directory has an entry, removed entries have no directories) or `ToolsTreeRelease`, or if a newly added entry breaks the onboarding rule (section 3). |

The `build-and-release.yml` matrix is generated from `os-lifecycle.toml`,
which lists every entry not marked `state = "removed"`. Adding or removing an
OS doesn't require editing the workflow.

### 4.4 Manual backstop

Each quarter, the owner reviews `os-lifecycle.toml` against vendor pages,
because endoflife.date is community-maintained and can lag announcements:

- Fedora: <https://fedorapeople.org/groups/schedule/>
- Ubuntu: <https://ubuntu.com/about/release-cycle>
- Debian: <https://wiki.debian.org/DebianReleases>, <https://wiki.debian.org/LTS>
- CentOS Stream: <https://www.centos.org/cl-vs-cs/>
- Rocky Linux: <https://wiki.rockylinux.org/rocky/version/>
- openSUSE Leap: <https://en.opensuse.org/Lifetime>

## 5. Lifecycle stages and timeline

```
 Supported ──T-90d──▶ Approaching EOL ──T──▶ Deprecated ──≤T+30d──▶ Removed ──▶ Archived
                      (tracking issue)       (EOL marker)           (code gone)  (results kept)
```

| Stage | Entered | Build | New certifications | Matrix | Required actions |
|---|---|---|---|---|---|
| **Supported** | Onboarded | Yes | Counted | ✅ / ❌ as earned | None |
| **Approaching EOL** | *T* − 90 days | Yes | Counted | Unchanged | Open the tracking issue. Start onboarding the successor (successor rule). Announce (section 7). |
| **Deprecated** | *T* | main/devel only, with a warning. Tag builds fail. | Recorded, labelled `os-eol`, **not counted** | README status becomes 🔚 (EOL), via the weekly PR | README notice (automatic). Open the removal PR. |
| **Removed** | Removal PR merged. Deadline: ***T* + 30 days, or before the next `v*` tag, whichever is first** | No | Not counted | README row removed. Rows moved to the archive table. | Removal checklist (section 8). Release notes. |
| **Archived** | After removal | No | n/a | Archive table only | None. Issues and old tagged-release assets are kept indefinitely. |

### Early removal (skip the grace period)

Remove immediately, then announce after the fact, if any of these apply:

- Upstream repositories are archived or moved and the build fails (Oracular
  precedent).
- The OS can no longer meet SEV-SNP prerequisites, such as a host or guest
  kernel older than 6.11 or missing QEMU/OVMF SNP support (Bookworm
  precedent).
- A known unpatched vulnerability in the image makes it unsafe to distribute.

### Extensions

You can keep an OS past *T* + 30 days only by recording a justification in
the tracking issue, such as a partner commitment, and getting maintainer
approval. Extensions are capped at *T* + 90 days. During an extension the OS
stays Deprecated: it is still marked 🔚 and is still excluded from tagged
releases.

## 6. Handling existing certification results

**Decision: flag and archive, don't delete.** A certificate is a historical
record that a specific image passed a specific level on specific hardware at a
specific time. That record stays true after the OS goes EOL, and partners
auditing past claims need it to stay reachable. It must not be presented as
current support.

### 6.1 Certification matrix

| Location | Change |
|---|---|
| `README.md` master table | At *T*, the weekly PR (`sync-docs`) sets the Status cell to 🔚 and lists the OS between the `os-lifecycle:upcoming-removals` markers under the legend. At removal, `drop` **deletes the row**. The README shows current support only. |
| `docs/certifications.md` per-processor tables | At removal, `drop` **moves** the row out of the per-processor table into the `# End-of-Life OS Certifications (Archived)` section at the end of the file. |

Archive table format:

```markdown
# End-of-Life OS Certifications (Archived)

These results were valid when issued. The OS no longer receives upstream
security updates and is no longer built or certified by sev-certify.
Images remain available in the tagged releases listed.

| OS | EOL date | Processor | Last certified | Last release with images | Archived |
|---|---|---|---|---|---|
| Fedora 41 | 2025-12-15 | EPYC 7003 | [c3.0.0-1](https://github.com/AMDEPYC/sev-certify/issues/353) | v3.0.0-1 | 2026-12-xx |
```

> **Constraint:** `update-certification-matrix.yml` finds tables by looking
> for lines that start with `AMD EPYC <series>`. No line in the archive
> section may start with that text, so the processor column uses
> `EPYC 7003`.

### 6.2 GitHub issues and labels

`os-eol-check.yml` does all of the following when the removal PR merges:

- Add the `os-eol` label to every `certificate` issue for the OS.
- Close the issues that are still open, with reason *completed*, and comment:
  `<OS> reached upstream EOL on <date> and was removed from sev-certify in
  #<PR>. This certification is archived; see docs/certifications.md#end-of-life-os-certifications-archived.`
- **Keep** the `os-<distro>-<release>` label so the issues stay searchable.
  Set the label description to `EOL <date> — archived`.

### 6.3 Superseded Rocky minor releases

For each processor, archive an older minor's row (for example, Rocky 10.1)
when a newer minor of the same major has a certificate on that processor:

```bash
python3 scripts/os_lifecycle.py archive --name "Rocky 10.1" --eol <next minor GA date> --last-release <tag>
```

If there's still no newer certificate 90 days after the new minor's GA,
the weekly PR marks the older rows 🔚 until one is earned. New certificates
for superseded minors aren't counted. The `rocky-10` image is not affected.

### 6.4 Release assets

- **`devel`**: no action needed. `create-release` deletes and recreates
  `devel` on every main push, so the assets disappear once the removal PR
  merges.
- **Tagged releases** (for example `v3.0.0-1`): **leave them unchanged.**
  They're the reproducible artefact behind each certificate. Edit the release
  notes to add a line: "Contains images for OSes now EOL: …".

## 7. Communication

| When | Channel | Content | Owner |
|---|---|---|---|
| *T* − 90 | GitHub tracking issue (`os-eol` label), created automatically | EOL date, planned removal date, successor plan, link to this policy | Automation, then the owner triages |
| *T* − 90 | Certification component Confluence page / internal stakeholder list (partner and OSV contacts, dispatch users, test labs) | Same content as the tracking issue. Ask whether anyone needs an extension (section 5). | Owner |
| *T* | `README.md`: "Upcoming removals" note under the matrix legend, and 🔚 on the row | "<OS> reached end-of-life on <date> and will be removed by <date>." | Automation (weekly `os-eol-check` PR), then the owner merges it |
| Removal | Removal PR (`chore: drop <os> <release> support (EOL <date>)`) linking the tracking issue | What was removed and where the archived results are | Implementer |
| Next `v*` tag | Release notes, generated by `create-release`: **Removed (end-of-life)** section (OSes removed since the previous tag) and **Approaching end-of-life** section | One line per OS with EOL date and archive link | Automation, then the release manager reviews |
| Removal | Close the tracking issue (automatic) and post a final note to the stakeholder list | Done | Owner |

Release notes don't need a Deprecated section: a tag can't be cut while a
Deprecated OS is still in the tree (section 4.3), so by then it's either
removed or still Supported/Approaching. Preview them with
`python3 scripts/os_lifecycle.py release-notes --tag <tag> --since <previous tag date>`.

## 8. Removal checklist

Run the `drop` command on a branch, then copy this list into the removal PR
description:

```bash
python3 scripts/os_lifecycle.py drop <key> [--last-release <tag>]   # e.g. drop fedora-41
```

**Always:**

- [ ] Delete `images/host-<os>/` and `images/guest-<os>/` (`drop`, using
      `git rm`). `images/Makefile` finds images by wildcard, and
      `embed-guest-image` derives the guest ID from
      `$DISTRIBUTION`/`$RELEASE`, so neither needs editing.
- [ ] Mark the entry `state = "removed"` in `images/os-lifecycle.toml`, with
      `removed` and `last_release` (`drop`; the default `last_release` is the
      latest `v*` tag). The entry stays so the guards still recognise its
      label. This also drops the OS from the generated build matrix.
- [ ] `README.md`: remove the matrix row and the "Upcoming removals" note (`drop`).
- [ ] `docs/certifications.md`: move the rows to the archive table (6.1) (`drop`).
- [ ] Update docs and examples that use the OS as a sample. `drop` lists the
      remaining references found with `git grep`. Use a long-lived release
      (for example an LTS) in examples so they need updating less often.
- [ ] After merge, check that `os-eol-check.yml` labelled and closed the
      certificate issues, updated the label description (6.2), and closed the
      tracking issue.

**Only when the last release of a distro family is dropped** (`drop` says so):

- [ ] Remove the distro-specific branches in
      `modules/build/host/format-qemu-binary/mkosi.postinst`,
      `modules/build/common/network-fix/`, and
      `modules/build/common/load-kernel-modules/mkosi.build`.
- [ ] Remove the install hints for the distro in `sev_verify/README.md` and
      `docs/how-to-generate-certs.md`.
- [ ] Update the distro list under "Naming" in `images/README.md`.

**Not needed:** `sev_verify` (the test harness and CLI) reads OS identity from
`/etc/os-release` at runtime and has no per-OS logic. `beacon-report.sh`
derives labels dynamically.

**Build infrastructure (tracked like an OS):**

- [ ] `ToolsTreeRelease` in `modules/build/common/mkosi.conf` must be bumped
      before its EOL, together with `[tools-tree]` in `os-lifecycle.toml`
      (`lint.yml` fails if they disagree).
- [ ] The CI runner workaround in `build-and-release.yml` (`sed
      noble→resolute` to get a recent mkosi) must be revisited when the
      runner image or the mkosi version changes.

## 9. Current status and first actions (as of 2026-11-24)

| Image | EOL (*T*) | *T* − 90 | Removal deadline | Stage | Action |
|---|---|---|---|---|---|
| fedora-41 | 2025-12-15 | — | **Overdue** | Removed (pending) | Remove now. Archive the 7003/9004 rows. Close #353, #334, #279. |
| ubuntu-25.04 | 2026-01-17 | — | **Overdue** | Removed (pending) | Remove now. Archive the rows. |
| ubuntu-25.10 | 2026-07-01 | — | **Overdue** | Removed (pending) | Remove now. Archive the rows. |
| rocky-10 (minors 10.0–10.2) | Superseded 2026-11-16 (10.3 GA) | — | Rows: 2027-02-14 if not recertified | Image Supported; rows Deprecated per 6.3 | Certify Rocky 10.3 on 7003/9004. Archive the 10.0, 10.1, and 10.2 rows. |
| fedora-44 | 2027-06-02 | 2027-03-04 | 2027-07-02 | Supported | Onboard Fedora 45 (EOL 2027-12-08). |
| tools tree (Fedora 44) | 2027-06-02 | 2027-03-04 | before *T* | Supported | Bump `ToolsTreeRelease` to 45 or later before 2027-06-02. |
| opensuse-16.0 | 2027-10-31 | 2027-08-02 | 2027-11-30 | Supported | Onboard Leap 16.1 when GA. |
| centos-10 (Stream) | 2030-05-31 | 2030-03-02 | 2030-06-30 | Supported | None |
| debian-13 | 2030-06-30 (security team ends 2028-08-09) | 2030-04-01 | 2030-07-30 | Supported | None |
| ubuntu-26.04 (LTS) | 2031-05-29 | 2031-02-28 | 2031-06-28 | Supported | None |
| rocky-10 (major) | 2035-05-31 | 2035-03-02 | 2035-06-30 | Supported | None |
| debian-forky | n/a (testing) | — | — | Supported | Rename to `debian-14` when Debian 14 is released. |

Dates come from the endoflife.date API, retrieved 2026-11-24.

## 10. Implementation follow-ups

Done:

- `images/os-lifecycle.toml` and `scripts/os_lifecycle.py`, with tests in
  `scripts/tests/` (4.1).
- The `os-eol-check.yml` scheduled workflow (4.2). It creates the `os-eol`
  label on its first run.
- EOL guards in `build-and-release.yml`, `images/Makefile`,
  `update-certification-matrix.yml`, and `lint.yml`, plus the generated build
  matrix (4.3).
- 🔚 in the README legend, the "Upcoming removals" markers, and the archive
  section in `docs/certifications.md`.
- The onboarding rule and a link to this policy in
  `docs/how-to-add-new-os-images.md`.
- Generated release notes in `create-release` (section 7).
- A manual test guide: [os-lifecycle-testing.md](os-lifecycle-testing.md).
- A description of the changes and how each part works:
  [os-lifecycle-changes.md](os-lifecycle-changes.md).

Remaining:

1. Remove Fedora 41, Ubuntu 25.04, and Ubuntu 25.10 with `drop` (section 8).
   Until then, `v*` tag builds fail by design.
2. Archive the Rocky 10.0–10.2 rows per processor once Rocky 10.3 is
   certified on it (6.3).

## 11. Open questions for maintainer sign-off

- **Grace period length.** Is 30 days right? It's short because Fedora and
  Ubuntu move EOL repos to archive mirrors within weeks, which breaks builds.
  Partners who need longer can use the last tagged release.
- **Debian EOL date.** Should Debian use the LTS end (`eolFrom`, as proposed)
  or the end of security-team support (`eoasFrom`)? The difference is about
  two years.
- **README behaviour during Deprecated.** Is showing 🔚 enough, or should the
  row be removed at *T*?
- **Re-cutting tags.** Should the `v3.0.0-1` release be re-cut without the
  EOL images, or kept unchanged as proposed?
