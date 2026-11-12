# ConfigFS-TSM (Trusted Security Module via ConfigFS)

**Description:** A Linux kernel interface that generates SEV-SNP attestation reports through the `/sys/kernel/config/tsm` filesystem. It provides a standardized, filesystem-based API for attestation that works across different confidential computing technologies, replacing the earlier `sev-guest` ioctl interface.  
**When to Use:** When generating attestation reports from inside an SNP guest using the Linux kernel's TSM (Trusted Security Module) framework. The configfs-tsm interface is the modern, kernel-standardized approach for attestation.  
**How to Use:** Mount configfs filesystem, create report directories under `/sys/kernel/config/tsm/report/`, write nonce data to `inblob`, read the generated attestation report from `outblob`.  
**What sev-certify tests:** Basic attestation report generation via configfs-tsm, generation counter tracking, certificate chain verification, and a Reported TCB check that confirms the attestation report reflects host TCB changes.

---

## What It Is

**ConfigFS-TSM** is a Linux kernel interface that exposes attestation report generation through the configfs virtual filesystem. Instead of using device-specific ioctls (like the older `/dev/sev-guest` interface), configfs-tsm provides a uniform, filesystem-based API that works across different [Trusted Security Module](#tsm) providers.

For SEV-SNP guests, the TSM backend is implemented by the `tsm_report` kernel module (or `tsm` on older kernels), which interfaces with the SEV-SNP firmware through the [sev-guest driver](#sev-guest-driver). The workflow is:

1. **Mount configfs** (if not already mounted): `mount -t configfs none /sys/kernel/config`
2. **Create a report directory**: `mkdir /sys/kernel/config/tsm/report/<name>` — each directory represents one attestation report request.
3. **Write request data**: Write a nonce (up to 64 bytes) to the `inblob` file. This becomes the attestation report's `REPORT_DATA` field.
4. **Track generation**: The `generation` file is a counter that starts at 0 and increments by 1 each time `inblob` is written, confirming the write succeeded.
5. **Read the report**: Reading `outblob` generates the attestation report and returns it as binary data.

The report structure, signing, and TCB fields are identical to those from `snpguest report` — configfs-tsm is simply a different interface to the same underlying firmware command (`SNP_GUEST_REQUEST` / `MSG_REPORT_REQ`).

### Key Components

| Component | Purpose |
|---|---|
| **`/sys/kernel/config/tsm/report/`** | Root directory for attestation report requests |
| **`<name>/`** | User-created directory representing one report request |
| **`<name>/inblob`** | Write-only file accepting the request data (nonce, up to 64 bytes) |
| **`<name>/outblob`** | Read-only file returning the generated attestation report |
| **`<name>/generation`** | Read-only counter tracking how many times `inblob` has been written |

---

## How to Use It

### Prerequisites

The guest kernel must have:
- **TSM Reports** enabled: `CONFIG_TSM_REPORTS=y` or `CONFIG_TSM_REPORTS=m`
- **SEV-guest driver** enabled and working (`snpguest ok` succeeds)
- **configfs** filesystem support

Modern SEV-SNP guest images include all of these by default.

### Basic Usage

```sh
# 1. Mount configfs if not already mounted
mount -t configfs none /sys/kernel/config
ls /sys/kernel/config/tsm  # Should show the tsm directory

# 2. Create a report request directory
mkdir /sys/kernel/config/tsm/report/my_report

# 3. Check initial generation (should be 0)
cat /sys/kernel/config/tsm/report/my_report/generation
# Output: 0

# 4. Write a nonce to trigger report generation
dd if=/dev/urandom bs=64 count=1 | tee /sys/kernel/config/tsm/report/my_report/inblob > /dev/null

# 5. Verify generation incremented
cat /sys/kernel/config/tsm/report/my_report/generation
# Output: 1

# 6. Read the attestation report
cat /sys/kernel/config/tsm/report/my_report/outblob > /root/attestation_report.bin

# 7. (Optional) Display the report with snpguest
snpguest display report /root/attestation_report.bin
```

### Multiple Reports

Each directory under `/sys/kernel/config/tsm/report/` is independent. To generate multiple reports (for example, before and after a TCB change):

```sh
# First report
mkdir /sys/kernel/config/tsm/report/baseline
dd if=/dev/urandom bs=64 count=1 | tee /sys/kernel/config/tsm/report/baseline/inblob > /dev/null
cat /sys/kernel/config/tsm/report/baseline/outblob > baseline.bin

# Second report with fresh nonce
mkdir /sys/kernel/config/tsm/report/after_change
dd if=/dev/urandom bs=64 count=1 | tee /sys/kernel/config/tsm/report/after_change/inblob > /dev/null
cat /sys/kernel/config/tsm/report/after_change/outblob > after_change.bin
```

Each directory's `generation` counter starts at 0 independently.

---

## How We Test It

The test is `configfs-tsm` at certification level `3.0.0-2`, defined in:

- **Test module:** [`sev_verify/cert_tests/c3_0/c3_0_0_2/configfs_tsm.py`](../../sev_verify/cert_tests/c3_0/c3_0_0_2/configfs_tsm.py)
- **Manifest entry:** [`sev_verify/cert_tests/c3_0/manifest.toml`](../../sev_verify/cert_tests/c3_0/manifest.toml)

It is a **mixed-scope** test that exercises both guest-side report generation and host-side verification. The test requires `--allow-host-changes` because it modifies the host's Reported TCB via `snphost config set`.

### Test Flow

**1. Baseline Setup**
   - Save the original host TCB to `host_tcb_v1.json` via `snphost show tcb -o json`
   - Launch an SNP-enabled guest

**2. Enablement Checks (Setup)**
   - Verify TSM module is present (`tsm` or `tsm_report` loaded, or `CONFIG_TSM_REPORTS=y`)
   - Verify sev-guest driver is working (`snpguest ok` succeeds)
   - Mount configfs filesystem and verify `/sys/kernel/config/tsm` exists

**3. First Attestation Report (Setup)**
   - Create report directory `/sys/kernel/config/tsm/report/configfs_tsm_attestation_report`
   - Verify `generation` starts at 0
   - Write 64-byte nonce to `inblob`
   - Verify `generation` incremented to exactly 1
   - Read `outblob` to generate the report binary
   - Pull the report to the host as `configfs_tsm_attestation_report.bin`

**4. Attestation Workflow Verification (Required)**
   - Display the report with `snpguest display report`
   - Fetch ARK/ASK certificates from KDS
   - Fetch the VCEK certificate for this report from KDS
   - Verify the certificate chain (VCEK signed by ASK, ASK signed by ARK)

**5. Reported TCB Check (Required)**
   - Lower the host's Reported TCB by reducing Boot Loader by 1 via `snphost config set`
   - Save the modified host TCB to `host_tcb_v2.json`
   - Verify the host's Reported TCB reflects the change while Platform TCB remains unchanged
   - Generate a second report from the **same live guest** via configfs-tsm:
     - Create `/sys/kernel/config/tsm/report/reported_tcb_check`
     - Verify generation tracking (0 → 1)
     - Write nonce, read outblob, pull `attestation_report_reported_tcb_test.bin`
   - Stop the VM (no longer needed for remaining checks)
   - Display the new report as JSON
   - Verify its certificate chain
   - **Assert** the new report's Reported TCB matches `host_tcb_v2.json` Reported TCB

**6. Cleanup**
   - Reset the host TCB with `snphost config reset`
   - Verify restoration with `snphost ok` (Reported TCB == Platform TCB)

### What is Checked

| Check | Validates |
|---|---|
| **TSM module presence** | Kernel has TSM support (`tsm` or `tsm_report` module, or built-in) |
| **sev-guest driver** | SEV-SNP guest firmware interface is working |
| **configfs mount** | configfs filesystem is available and tsm tree is accessible |
| **Generation tracking** | Report directory starts at generation 0, increments exactly by 1 after `inblob` write |
| **Report generation** | Reading `outblob` produces a valid binary attestation report |
| **Certificate chain** | Report is signed by VCEK, which chains to ASK and ARK |
| **Reported TCB reflection** | A live guest's new report reflects host TCB changes made via `snphost config set` |
| **TCB restoration** | `snphost config reset` restores Reported TCB to Platform TCB |

The test uses **two independent report directories** to show that:
1. The generation counter is per-directory (each starts at 0)
2. A live guest can generate multiple reports
3. Later reports from the same guest reflect host TCB changes made after the guest launched

### Severities

- **Setup steps**: Baseline TCB save, VM launch, enablement checks, first report generation. A failure skips the rest of the test.
- **Required steps**: Attestation workflow, TCB modification, second report generation, TCB check, cleanup. A failure is reported but subsequent steps still run (except when a callable step halts).
- **Info steps**: VM stop. Logged but does not affect pass/fail.

### Artifacts

All artifacts land in `artifacts/3.0/3.0.0-2/configfs_tsm/`:

| File | Contents |
|---|---|
| `host_tcb_v1.json` | Original host TCB before any changes |
| `host_tcb_v2.json` | Host TCB after Boot Loader lowered by 1 |
| `configfs_tsm_attestation_report.bin` | First attestation report (baseline) |
| `configfs_tsm_attestation_report.log` | Human-readable display of first report |
| `attestation_report_reported_tcb_test.bin` | Second report (after TCB change) |
| `attestation_report_reported_tcb_test.json` | JSON display of second report |
| `certs/` | ARK, ASK, VCEK for first report |
| `certs_reported_tcb/` | ARK, ASK, VCEK for second report |

With `--debug`, the `configfs-tsm-vm/` directory contains QEMU logs.

---

## Architecture & Workflow

### Kernel Components

```
┌─────────────────────────────────────────────────────────────┐
│ User Space (Guest)                                          │
│  /sys/kernel/config/tsm/report/<name>/                     │
│    ├─ inblob   (write nonce)                               │
│    ├─ outblob  (read attestation report)                   │
│    └─ generation (read counter)                            │
└──────────────────────┬──────────────────────────────────────┘
                       │ configfs API
┌──────────────────────▼──────────────────────────────────────┐
│ Kernel Space                                                │
│  ┌────────────────────────────────────┐                     │
│  │ configfs (CONFIG_CONFIGFS_FS)      │                     │
│  └────────────┬───────────────────────┘                     │
│               │                                             │
│  ┌────────────▼───────────────────────┐                     │
│  │ tsm_report / tsm                   │                     │
│  │ (CONFIG_TSM_REPORTS)               │                     │
│  │ - TSM framework core               │                     │
│  │ - Provider registration            │                     │
│  └────────────┬───────────────────────┘                     │
│               │                                             │
│  ┌────────────▼───────────────────────┐                     │
│  │ sev-guest (SEV-SNP TSM provider)   │                     │
│  │ - Implements TSM callbacks         │                     │
│  │ - Wraps SNP_GUEST_REQUEST          │                     │
│  └────────────┬───────────────────────┘                     │
└───────────────┼─────────────────────────────────────────────┘
                │ SNP_GUEST_REQUEST (MSG_REPORT_REQ)
┌───────────────▼─────────────────────────────────────────────┐
│ PSP Firmware                                                │
│  - Generate attestation report                             │
│  - Sign with VCEK (derived from ReportedTcb)               │
│  - Return ATTESTATION_REPORT structure                     │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **User writes to `inblob`**:
   - configfs receives the write
   - TSM framework validates the input (up to 64 bytes)
   - SEV-guest TSM provider stores the nonce and increments `generation`

2. **User reads from `outblob`**:
   - configfs triggers TSM report generation
   - SEV-guest provider issues `SNP_GUEST_REQUEST` with `MSG_REPORT_REQ`
   - PSP firmware generates the attestation report:
     - Includes guest's `MEASUREMENT`, `POLICY`, `FAMILY_ID`, `IMAGE_ID`, `GUEST_SVN`
     - Includes TCB versions: `CURRENT_TCB` (captured at launch as `LAUNCH_TCB`), `REPORTED_TCB` (host's current Reported TCB)
     - Signs with VCEK derived from `REPORTED_TCB`
     - Embeds the nonce as `REPORT_DATA`
   - Report returned to user space

3. **Host verification**:
   - Fetch VCEK certificate for the report's `REPORTED_TCB` from AMD KDS
   - Verify report signature with VCEK public key
   - Verify VCEK → ASK → ARK certificate chain

---

## Comparison with `sev-guest` ioctl

configfs-tsm and the older `/dev/sev-guest` ioctl both generate attestation reports, but differ in interface and portability:

| Aspect | configfs-tsm | `/dev/sev-guest` ioctl |
|---|---|---|
| **Interface** | Filesystem (`/sys/kernel/config`) | Character device (`/dev/sev-guest`) |
| **API Style** | Uniform across TSM providers | SEV-specific ioctl commands |
| **Kernel Config** | `CONFIG_TSM_REPORTS`, `CONFIG_CONFIGFS_FS` | `CONFIG_SEV_GUEST` |
| **User Tools** | Standard shell (`cat`, `dd`, `tee`) | Device-specific tools (`snpguest`) |
| **Portability** | Works with TDX, SEV, other TSM providers | SEV-only |
| **Adoption** | Modern, recommended for new code | Legacy, still supported |
| **Report Format** | Identical (same firmware command) | Identical |

Both produce the same `ATTESTATION_REPORT` structure and are signed by the same VCEK. Verifiers cannot distinguish which interface was used.

---

## What is Not Covered

- **Extended report requests** (requesting certificates alongside the report) are not tested. configfs-tsm supports reading `auxblob` to retrieve certificates, but the test fetches certificates from KDS instead.
- **Multiple writes to `inblob`** in the same directory are not tested (only 0 → 1 generation transitions are checked).
- **Report size limits** and malformed input handling are not explicitly tested.
- **TDX or other TSM providers** are not covered; the test is SEV-SNP specific.
- **Live TCB changes visible in `CURRENT_TCB`** are not covered. The test checks `REPORTED_TCB` (host-controlled) but does not verify that `CURRENT_TCB` in the report equals `LAUNCH_TCB` captured at launch.

---

## Known Quirks

- **Generation counter does not reset.** Once a directory is created, its `generation` starts at 0, but removing and recreating the directory resets it. The test relies on fresh directories (generation 0) for each report.
- **Module name varies by kernel version.** Older kernels use `tsm`, newer ones use `tsm_report`. The test checks for both, and also accepts `CONFIG_TSM_REPORTS=y` (built-in, no module).
- **No sudo in guest.** The guest image runs the agent as root, so `sudo` is not used in guest commands. If testing on a non-root guest, prefix the configfs commands with `sudo`.
- **snphost `-o json` requires a recent build.** The test uses `snphost show tcb -o json` and will fail with older builds. Set `$SNPHOST` to point to a newer binary if the system `snphost` lacks JSON output support.

---

## Glossary

<a id="configfs"></a>
**configfs** — A Linux virtual filesystem (`CONFIG_CONFIGFS_FS`) that exposes kernel subsystem configuration as a filesystem hierarchy. Users create directories and write files to configure subsystems. For TSM, configfs provides the `/sys/kernel/config/tsm/report/` interface.

<a id="generation"></a>
**generation** — A per-report-directory counter tracking how many times `inblob` has been written. Starts at 0, increments by 1 per write. Used to confirm that a write succeeded without reading the potentially large `outblob`.

<a id="inblob"></a>
**inblob** — The write-only file in a configfs-tsm report directory. Writing a nonce (up to 64 bytes) here triggers report generation. The nonce becomes the report's `REPORT_DATA` field.

<a id="outblob"></a>
**outblob** — The read-only file in a configfs-tsm report directory. Reading it returns the binary attestation report. The report is regenerated on each read (generation does not increment; only `inblob` writes bump it).

<a id="reported-tcb"></a>
**Reported TCB** — The TCB version the host reports via `snphost show tcb`. It determines which VCEK signs attestation reports. Can be lowered via `snphost config set` for VCEK management (the change is in-memory only and resets on reboot). See the [TCB Config & Commit](tcb-config-commit.md) feature page.

<a id="sev-guest-driver"></a>
**sev-guest driver** — The Linux kernel driver (`/dev/sev-guest`, `CONFIG_SEV_GUEST`) that interfaces with the SEV-SNP firmware. It provides both the ioctl interface and the TSM provider backend for configfs-tsm.

<a id="tsm"></a>
**TSM (Trusted Security Module)** — A Linux kernel framework that provides a uniform interface for attestation across different confidential computing technologies (SEV-SNP, TDX, etc.). Each technology provides a TSM "provider" that implements the framework's callbacks.

<a id="vcek"></a>
**VCEK (Versioned Chip Endorsement Key)** — An attestation signing key derived from chip-unique secrets and a TCB version. The VCEK corresponding to a report's `REPORTED_TCB` signs that report. Verifiers fetch the matching VCEK certificate from AMD's [Key Distribution Service (KDS)](#kds) to validate the signature. See the [TCB Config & Commit](tcb-config-commit.md#vcek) feature page.

<a id="kds"></a>
**KDS (Key Distribution Service)** — AMD's public service (`kdsintf.amd.com`) that distributes VCEK certificates. Given a chip ID and TCB version, it returns the corresponding VCEK certificate. See the [TCB Config & Commit](tcb-config-commit.md#kds) feature page.
