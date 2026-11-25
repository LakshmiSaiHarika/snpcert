# Manually testing the OS lifecycle (EOL) changes

This guide shows how to check, by hand, that the
[OS lifecycle policy](os-lifecycle-policy.md) is implemented correctly. It
covers:

| Component | What it does |
|---|---|
| [`images/os-lifecycle.toml`](../images/os-lifecycle.toml) | EOL data for every image and the build tools tree |
| [`scripts/os_lifecycle.py`](../scripts/os_lifecycle.py) | All lifecycle logic (`status`, `check`, `guard`, `label-status`, `matrix`, `validate`, `sync-docs`, `release-notes`, `archive`, `drop`) |
| `.github/workflows/os-eol-check.yml` | Weekly check: update PR, tracking issues, archiving certificate issues |
| `.github/workflows/build-and-release.yml` | `lifecycle` job: EOL gate, generated build matrix, release notes |
| `.github/workflows/update-certification-matrix.yml` | Keeps EOL results out of the matrix |
| `.github/workflows/lint.yml` | `os lifecycle` job: unit tests, validation, onboarding rule |
| `images/Makefile` | `make lifecycle`, warning when building an EOL image |

There are two parts:

- **[Part A](#part-a-local-tests)** runs locally in a few minutes and needs
  nothing but Python 3.11+ and git. It covers all of the script's logic.
- **[Part B](#part-b-github-tests-in-a-fork)** runs the workflows in a fork,
  because it creates issues, labels, PRs, tags and releases. Never run Part B
  against `AMDEPYC/sev-certify`.

Record the results in the [checklist](#results-checklist) at the end.

> **Dates.** Expected output below assumes the data as of 2026-11-24, when
> Fedora 41, Ubuntu 25.04 and Ubuntu 25.10 are past their removal deadlines.
> The local tests pin the date with `--today` so they give the same result on
> any day. The `check` tests and Part B use live endoflife.date data and
> today's date, so dates and stages may differ slightly from the examples.

## Part A: local tests

### A.0 Set up a scratch copy

`drop`, `archive` and `sync-docs` edit files and delete image directories, so
run everything in a disposable worktree, not your working copy:

```bash
cd sev-certify
git worktree add --detach /tmp/eol-test HEAD
cd /tmp/eol-test
S=scripts/os_lifecycle.py
T=--today=2026-11-24
G="git -c user.email=test@example.com -c user.name=test"
```

The changes must be committed for the worktree to contain them. Remove the
worktree when done ([cleanup](#cleanup)).

### A.1 Unit tests

```bash
python3 -m unittest discover -s scripts/tests -v
```

**Expected:** `Ran 30 tests ... OK`. Lines starting with `warning:`, `error:`
or `notice:` are expected guard output from the tests, not failures.

### A.2 Lifecycle stages

```bash
python3 $S $T status
```

**Expected:** Fedora 41, Ubuntu 25.04 and Ubuntu 25.10 are
`Deprecated (removal overdue)`. Everything else is `Supported`, and
`debian-forky` has EOL `none`.

Move the date to check each stage boundary for Fedora 44 (EOL 2027-06-02):

```bash
for d in 2027-03-03 2027-03-04 2027-06-01 2027-06-02 2027-07-02 2027-07-03; do
  echo -n "$d  "; python3 $S --today=$d status | grep fedora-44
done
```

**Expected:**

| `--today` | Stage | Policy rule |
|---|---|---|
| 2027-03-03 | Supported | before *T* − 90 |
| 2027-03-04 | Approaching EOL | *T* − 90 |
| 2027-06-01 | Approaching EOL | |
| 2027-06-02 | Deprecated | *T* |
| 2027-07-02 | Deprecated | *T* + 30 (last day of grace) |
| 2027-07-03 | Deprecated (removal overdue) | after *T* + 30 |

The `tools-tree` entry has no grace period, because the tools tree must be
bumped *before* its EOL. Its removal deadline is the EOL date itself, so it is
`Deprecated` on 2027-06-02 and `Deprecated (removal overdue)` from 2027-06-03.
Strict guards fail from 2027-06-02.

`make lifecycle`, run in `images/`, prints the same table as `status`.

### A.3 Build guard

```bash
python3 $S $T guard --all;            echo "rc=$?"
python3 $S $T guard --all --strict;   echo "rc=$?"
python3 $S $T guard fedora-44 --strict; echo "rc=$?"
python3 $S --today=2027-03-04 guard fedora-44; echo "rc=$?"
GITHUB_ACTIONS=true python3 $S $T guard fedora-41
```

**Expected:**

1. Three `warning:` lines (Fedora 41, Ubuntu 25.04, Ubuntu 25.10), each with
   the `drop` command to run, and `rc=0`. This is what main/PR builds do.
2. The same three as `error: ... EOL images must not ship in a tagged release.`
   and `rc=1`. This is what tag builds do.
3. No output, `rc=0`.
4. Two `notice:` lines (Fedora 44 and the build tools tree), `rc=0`.
5. A GitHub annotation:
   `::warning file=images/os-lifecycle.toml::Fedora 41 reached end-of-life ...`

**Makefile:** in `images/`, with a `.tags` file present (`make tags`), run
`make host-fedora-41`. The `warning: Fedora 41 reached end-of-life ...` line
must print before mkosi starts. Press Ctrl-C once mkosi starts; you don't need
the full build.

### A.4 Build matrix

```bash
python3 $S matrix | python3 -m json.tool
```

**Expected:** `{"include": [...]}` with 10 `distro`/`release` pairs, one per
`images/host-*` directory. No `tools-tree`. Releases are strings
(`"16.0"`, not `16`).

### A.5 Validation

Run each case, then restore with `git checkout -- . && git clean -fdq images`.

| Case | Commands | Expected |
|---|---|---|
| Clean tree | `python3 $S $T validate` | `images/os-lifecycle.toml: OK (11 entries)`, rc 0 |
| Image without entry | `mkdir -p images/{host,guest}-ubuntu-27.04 && touch images/{host,guest}-ubuntu-27.04/mkosi.conf && python3 $S $T validate` | `error: images/*-ubuntu-27.04 has no [ubuntu-27.04] entry ...`, rc 1 |
| Onboarding rule, too close to EOL | After the previous row, add the entry below with `eol = 2027-03-01`, then `python3 $S $T validate --base-ref HEAD` | `error: [ubuntu-27.04]: new OS releases need at least 180 days to EOL; Ubuntu 27.04 has 97 ...`, rc 1 |
| Onboarding rule, enough time | Change that `eol` to `2028-01-15` and rerun | `OK (12 entries)`, rc 0. `python3 $S matrix` now includes `"27.04"` |
| Tools tree mismatch | `sed -i 's/^ToolsTreeRelease=44/ToolsTreeRelease=45/' modules/build/common/mkosi.conf && python3 $S $T validate` | `error: [tools-tree] says fedora 44 but modules/build/common/mkosi.conf uses fedora 45`, rc 1 |
| Extension over the cap | Add `extended_until = 2026-06-01` under `[fedora-41]`, then `python3 $S $T validate` | `error: [fedora-41]: extended_until is capped at eol + 90 days (2026-03-15)`, rc 1 |
| Typo in a field | Add `eoll = 2027-01-01` under `[fedora-44]`, then `python3 $S status` | `error: [fedora-44]: unknown field(s) eoll`, rc 1 |

Entry for the onboarding rows:

```toml
["ubuntu-27.04"]
product = "ubuntu"
cycle = "27.04"
eol = 2027-03-01
os_label = "os-ubuntu-27.04"
```

### A.6 Upstream check (needs network)

```bash
python3 $S check --summary
```

**Expected:** a markdown summary with a table of all 11 entries. If upstream
dates match the cache, there's no "Upstream changes" section. Under "New
releases eligible for onboarding" it should list Fedora 45 and Ubuntu 26.10,
or whatever is newer when you test.

Simulate a stale cached date and check that `--update` fixes it:

```bash
sed -i '/^\[fedora-44\]/,/^$/s/^eol = .*/eol = 2027-05-01/' images/os-lifecycle.toml
python3 $S check --update --summary | grep -A3 "Upstream changes"
grep -A3 '^\[fedora-44' images/os-lifecycle.toml | grep eol
git status --short images/os-lifecycle.toml
```

**Expected:** `` `fedora-44`: eol 2027-05-01 → 2027-06-02 ``, the file is back
to `eol = 2027-06-02`, and `git status` shows no change. The update edits only
that one line and keeps comments and layout.

Check the tracking-issue content the workflow will post:

```bash
python3 $S check --report /tmp/eol-report.json > /dev/null
python3 -c "
import json; r = json.load(open('/tmp/eol-report.json'))
for e in r['entries']:
    if e['issue_title']: print(e['issue_title']); print(e['issue_body']); print('-' * 40)"
```

**Expected:** one issue each for Fedora 41, Ubuntu 25.04 and Ubuntu 25.10. Each
body starts with `<!-- os-lifecycle key=<key> stage=overdue -->` and has a
dates table, successor candidates (Fedora 45 for Fedora 41), and the `drop`
command. Approaching-EOL bodies (try `--today=2027-03-04`) also include the
onboarding and announcement steps.

### A.7 Certification matrix guard

```bash
for l in os-fedora-41 os-fedora-44 os-rocky-10.2 os-rocky-10.3 os-opensuse-leap-16.0 os-ubuntu-24.04; do
  printf '%-24s ' "$l"; python3 $S $T label-status "$l" --github-output | grep -E '^(stage|counted)=' | tr '\n' ' '; echo
done
```

**Expected:**

| Label | `stage` | `counted` | Why |
|---|---|---|---|
| `os-fedora-41` | `overdue` | `false` | Past EOL |
| `os-fedora-44` | `supported` | `true` | |
| `os-rocky-10.2` | `superseded` | `false` | Rocky 10.3 is GA |
| `os-rocky-10.3` | `supported` | `true` | Latest minor |
| `os-opensuse-leap-16.0` | `supported` | `true` | Label differs from the image key |
| `os-ubuntu-24.04` | `untracked` | `true` | Not in the file; existing behaviour is kept |

Results with `counted=false` also include a one-line `reason=` used in the
issue comment.

### A.8 README sync

```bash
python3 $S $T sync-docs && git diff README.md
python3 $S $T sync-docs   # second run
$G commit -qam "sync-docs"
```

**Expected:** the first run prints `Updated README.md`. The diff:

- sets the Status cell of Fedora 41, Ubuntu 25.04 and Ubuntu 25.10 to 🔚 and
  leaves the certificate links unchanged;
- adds an **Upcoming removals** list with the three OSes and their removal
  deadlines between the `os-lifecycle:upcoming-removals` markers.

The second run prints nothing, because the sync is idempotent.

Superseded Rocky minors are marked 90 days after the next minor's GA
(policy 6.3):

```bash
python3 $S --today=2027-02-13 sync-docs; git diff --stat README.md   # no change
python3 $S --today=2027-02-14 sync-docs; git diff README.md | grep '^[-+]| Rocky'
git checkout -- README.md
```

**Expected:** no change on 2027-02-13. On 2027-02-14, Rocky 10.1 and 10.2
become 🔚 in the README.

### A.9 Removing an OS (`drop`)

```bash
python3 $S $T drop ubuntu-25.04 --last-release v3.0.0-1
git status --short
git diff -- README.md docs/certifications.md images/os-lifecycle.toml
```

**Expected:**

- Output lists follow-ups: `Archived 2 row(s)`, a note that the workflow will
  archive the `os-ubuntu-25.04` issues after merge, and
  `Suggested PR title: chore: drop Ubuntu 25.04 support (EOL 2026-01-17)`.
- `images/host-ubuntu-25.04/` and `images/guest-ubuntu-25.04/` are deleted and
  staged (`D `).
- `[ubuntu-25.04]` gains `state = "removed"`, `removed = 2026-11-24` and
  `last_release = "v3.0.0-1"`.
- `README.md`: the Ubuntu 25.04 row is gone and it's no longer listed under
  Upcoming removals.
- `docs/certifications.md`: the EPYC 7003 and 9004 rows are moved to the end
  of the file under *End-of-Life OS Certifications (Archived)*, for example
  `| Ubuntu 25.04 | 2026-01-17 | EPYC 7003 | [c3.0.0-1](...issues/346) | v3.0.0-1 | 2026-11-24 |`.
  No archive line starts with `AMD EPYC`, which keeps
  `update-certification-matrix.yml` from finding it.

Then check the rest of the tooling sees the removal:

```bash
python3 $S $T validate                                   # OK (11 entries)
python3 $S matrix | grep -c '25.04'                      # 0
python3 $S $T label-status os-ubuntu-25.04 --github-output | grep -E 'stage|counted'
                                                         # stage=removed, counted=false
python3 $S $T guard --all --strict; echo "rc=$?"         # only Fedora 41 / Ubuntu 25.10 errors
python3 $S $T drop ubuntu-25.04; echo "rc=$?"            # error: [ubuntu-25.04] is already removed, rc=1
$G commit -qam "drop ubuntu-25.04"
```

Next, check the follow-up hints, then discard these two drops so A.10 and
A.11 start from the state above:

```bash
python3 $S $T drop fedora-41 --last-release v3.0.0-1
python3 $S $T drop fedora-44 --last-release v3.0.0-1
git reset -q --hard HEAD
```

**Expected:**

- `drop fedora-41` lists the remaining Fedora 41 references in
  `images/README.md`, `docs/how-to-run-guest-manually.md`,
  `modules/build/guest/README.md` and
  `modules/test/guest/attestation-workflow/README.md`. Fedora 44 remains, so it
  must **not** print *"This was the last fedora release"*.
- `drop fedora-44` prints *"This was the last fedora release"* and lists the
  distro-specific `modules/build/...` files to clean up. The `tools-tree`
  entry doesn't count as a Fedora image.

### A.10 Archiving superseded Rocky rows

```bash
python3 $S $T archive --name "Rocky 10.0" --eol 2025-11-25 --last-release v3.0.0-1
tail -4 docs/certifications.md
```

**Expected:** `Archived 1 row(s) for Rocky 10.0`, and the last row is
`| Rocky 10.0 | 2025-11-25 | EPYC 7003 | N/A | v3.0.0-1 | 2026-11-24 |`.
Rocky 10.1 and 10.2 rows stay where they were.

### A.11 Release notes

After A.9 (Ubuntu 25.04 removed on 2026-11-24):

```bash
python3 $S --today=2027-04-01 release-notes --tag v3.1.0 --since 2026-11-04
python3 $S --today=2027-04-01 release-notes --tag v3.2.0 --since 2026-12-01
```

**Expected:** the first command prints:

```
OS images for v3.1.0 certification.

### Removed (end-of-life)

- Ubuntu 25.04: upstream EOL 2026-01-17, removed 2026-11-24. Last release with images: v3.0.0-1. Results are archived in [docs/certifications.md](...)

### Approaching end-of-life

- Fedora 44: upstream EOL 2027-06-02. It will not be included in releases tagged after that date.
```

The second has no "Removed" section, because the removal predates `--since`.

## Part B: GitHub tests in a fork

These tests create real issues, labels, PRs, tags and releases. Use a
personal fork, never `AMDEPYC/sev-certify`.

### B.0 Prepare the fork

1. Fork `AMDEPYC/sev-certify`, or sync an existing fork, then push the
   branch with these changes to the fork's `main`:
   ```bash
   git push <fork-remote> HEAD:main
   ```
2. In the fork's **Settings**:
   - **General → Features:** enable Issues.
   - **Actions → General:** allow all actions. Under **Workflow
     permissions**, choose *Read and write permissions* and tick *Allow
     GitHub Actions to create and approve pull requests*.
3. Create the labels and milestone the tests use, if the fork lacks them.
   `os-eol` is created automatically by `os-eol-check`.
   ```bash
   R=<you>/<fork>
   for l in certificate os-fedora-41 os-fedora-44 os-rocky-10.2 proc-7003; do gh label create "$l" -R $R --force; done
   gh api repos/$R/milestones -f title=c3.0.0-1
   ```

Scheduled workflows don't run in forks by default, so start `os-eol-check`
with **Run workflow** (or `gh workflow run os-eol-check -R $R`).

### B.1 `lint`: `os lifecycle` job

1. Open a PR in the fork that only touches a doc. The **os lifecycle** job
   passes, its log shows `Ran 30 tests ... OK` and
   `images/os-lifecycle.toml: OK`, and the `status` table is printed.
2. Push a commit to the PR that adds `images/{host,guest}-ubuntu-27.04/mkosi.conf`
   (copy from `ubuntu-26.04`) and the `["ubuntu-27.04"]` entry from A.5 with
   `eol = 2027-03-01`. **Expected:** the job fails with the onboarding-rule
   error.
3. Change `eol` to a date more than 180 days away. **Expected:** the job
   passes. In the same PR, the `build-images` workflow's matrix now includes
   `ubuntu-27.04`.
4. Remove the entry but keep the directories. **Expected:** fails with
   `has no [ubuntu-27.04] entry`. Close the PR without merging.

### B.2 `build-images`: main and PR builds

Look at the run triggered by B.0's push to `main` (or any PR):

- A new **os lifecycle** job runs first. Its annotations show three warnings
  for Fedora 41, Ubuntu 25.04 and Ubuntu 25.10, and the job passes.
- The build jobs have the same names as before (`centos-10`, `debian-13`,
  `debian-forky`, `fedora-41`, `fedora-44`, `opensuse-16.0`, `rocky-10`,
  `ubuntu-25.04`, `ubuntu-25.10`, `ubuntu-26.04`). There are 10, all starting
  after the os lifecycle job.
- On `main`, `devel` is recreated with its usual notes.

You can cancel the run once the build jobs have started.

### B.3 `build-images`: tag builds are blocked by EOL images

```bash
git tag v0.0.1-eoltest && git push <fork-remote> v0.0.1-eoltest
```

**Expected:** the **os lifecycle** job fails with three `error` annotations
ending in `EOL images must not ship in a tagged release.` The
`create release` and build jobs are skipped. **No** `v0.0.1-eoltest` release
appears under Releases.

Clean up: `git push <fork-remote> :refs/tags/v0.0.1-eoltest && git tag -d v0.0.1-eoltest`.

### B.4 `os-eol-check`: first run

Run the workflow manually. **Expected:**

1. **Run summary** has the lifecycle table, any upstream changes, and the
   onboarding candidates, as in A.6.
2. **PR** `chore: update OS lifecycle data`, from branch
   `os-lifecycle/auto-update` and labelled `os-eol`. It changes `README.md`
   as in A.8 (🔚 rows and Upcoming removals), plus `os-lifecycle.toml` only if
   upstream data changed. Workflow-created PRs don't trigger other workflows;
   close and reopen the PR to run `lint`.
3. **Label** `os-eol` exists.
4. **Issues** `EOL: Fedora 41 (2025-12-15)`, `EOL: Ubuntu 25.04 (2026-01-17)`
   and `EOL: Ubuntu 25.10 (2026-07-01)`, each labelled `os-eol`, with the body
   from A.6. Links point to *your fork*.

### B.5 `os-eol-check`: idempotency and stage changes

1. Run the workflow again. **Expected:** no new issues, no new comments, and
   the PR is unchanged (or updated in place, never duplicated).
2. Edit one tracking issue's body and change the hidden marker from
   `stage=overdue` to `stage=approaching`. Run the workflow. **Expected:** the
   body is restored and one comment appears:
   *"⚠️ The removal deadline for **…** has passed … Remove it now:
   `python3 scripts/os_lifecycle.py drop …`."*
3. Change an issue's title and run again. **Expected:** the title is
   restored. Issues are matched by the hidden marker, not the title.

### B.6 `update-certification-matrix`: EOL results aren't counted

The workflow runs only for issues **opened** with the `certificate` label
and a milestone, as dispatch creates them:

```bash
gh issue create -R $R --title "fedora 41 SEV version 3.0 (EOL test)" --body "test" \
  --label certificate --label os-fedora-41 --label proc-7003 --milestone c3.0.0-1
```

**Expected:** the **Record EOL certification** step runs and
**Update Certification Matrix** is skipped. The issue gets the `os-eol` label
and a comment: *"Fedora 41 reached upstream end-of-life on 2025-12-15 … This
result is recorded but not added to the certification matrix …"*. No
`update-certification-matrix` PR is opened or updated.

Repeat with `os-rocky-10.2`. **Expected:** the same, with the reason
*"Rocky 10.2 was superseded by Rocky 10.3 (GA 2026-11-16)."*

Repeat with `os-fedora-44`. **Expected:** previous behaviour, so no `os-eol`
label, and the `update-certification-matrix` PR updates the Fedora 44 row.
The workflow's `close-duplicates` job may close earlier test issues for the
same OS and processor; that's existing behaviour.

### B.7 Removal flow end to end

1. Create a certificate issue to be archived, **without** a milestone so the
   matrix workflow ignores it:
   ```bash
   gh issue create -R $R --title "fedora 41 cert (archive test)" --body "test" \
     --label certificate --label os-fedora-41 --label proc-7003
   ```
2. On a branch, run `python3 scripts/os_lifecycle.py drop fedora-41`, fix the
   doc references it lists, commit
   (`chore: drop Fedora 41 support (EOL 2025-12-15)`), and open a PR.
   **Expected:** `lint` passes, and `build-images` no longer has a
   `fedora-41` job.
3. Merge the PR. The push to `main` changes `os-lifecycle.toml`, which
   starts `os-eol-check`. **Expected:**
   - `EOL: Fedora 41 (2025-12-15)` is closed as *completed*, with the comment
     *"Fedora 41 was removed on <date>. Closing."*
   - Every `certificate` + `os-fedora-41` issue, open or closed, has the
     `os-eol` label. The open ones, including the step 1 issue, are closed as
     *completed* with the archive comment linking to the archive section of
     `docs/certifications.md`.
   - The `os-fedora-41` label description is `EOL 2025-12-15 — archived`.
   - The Fedora 41 and Ubuntu tracking issues are otherwise unchanged.
4. Open a new certificate issue with `os-fedora-41` and milestone `c3.0.0-1`
   as in B.6. **Expected:** not counted, with a reason that says *"was removed
   from sev-certify on <date>"*.

### B.8 Release notes on a tag

Only possible once no image is past EOL. In the fork, also drop
`ubuntu-25.04` and `ubuntu-25.10` (B.7 step 2), merge, then:

```bash
git pull && git tag v0.0.2-eoltest && git push <fork-remote> v0.0.2-eoltest
```

**Expected:** the **os lifecycle** job passes (notices only, if anything is
approaching EOL). A `v0.0.2-eoltest` release is created whose notes start
with `OS images for v0.0.2-eoltest certification.` and include a
**Removed (end-of-life)** section listing Fedora 41, Ubuntu 25.04 and
Ubuntu 25.10, each with *Last release with images*. The section lists only
removals since the previous `v*` tag. You can cancel the image builds once the
release exists.

Clean up: `gh release delete v0.0.2-eoltest -R $R --cleanup-tag --yes`.

## Cleanup

```bash
cd sev-certify
git worktree remove --force /tmp/eol-test
```

In the fork, close or delete the test issues, PRs, tags and releases, or
delete the fork.

## Results checklist

| # | Test | Pass/Fail | Notes |
|---|---|---|---|
| A.1 | Unit tests | | |
| A.2 | Stage boundaries, `make lifecycle` | | |
| A.3 | Build guard (warn, strict, notice, annotation, Makefile) | | |
| A.4 | Build matrix JSON | | |
| A.5 | Validation errors and onboarding rule | | |
| A.6 | Upstream check, `--update`, tracking-issue content | | |
| A.7 | `label-status` classification | | |
| A.8 | README sync, idempotency, Rocky 6.3 | | |
| A.9 | `drop` | | |
| A.10 | `archive` | | |
| A.11 | Release notes | | |
| B.1 | `lint` os lifecycle job | | |
| B.2 | Main/PR build: warnings and generated matrix | | |
| B.3 | Tag build blocked by EOL images | | |
| B.4 | `os-eol-check` first run: PR, label, tracking issues | | |
| B.5 | `os-eol-check` idempotency and stage comment | | |
| B.6 | EOL/superseded results kept out of the matrix | | |
| B.7 | Removal flow: tracking issue closed, certificates archived | | |
| B.8 | Release notes on a tag | | |
