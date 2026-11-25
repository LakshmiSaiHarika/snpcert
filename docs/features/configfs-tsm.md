# ConfigFS-TSM (Trusted Security Module via ConfigFS)

**Description:** A Linux kernel interface that generates SEV-SNP attestation reports through the `/sys/kernel/config/tsm` filesystem. It provides a filesystem-based API for attestation that works across different confidential computing technologies. The older `/dev/sev-guest` ioctl interface remains supported.
**When to Use:** When generating attestation reports from inside an SNP guest using the Linux kernel's TSM (Trusted Security Module) framework.
**How to Use:** Mount configfs, create a report directory under `/sys/kernel/config/tsm/report/`, write a nonce to `inblob`, read the report from `outblob`.
**What sev-certify tests:** Report generation via configfs-tsm, generation counter tracking, certificate chain, report signature and `REPORT_DATA` verification, and a Reported TCB check that confirms a live guest's report reflects a host TCB change.

---

## What It Is

**ConfigFS-TSM** is a Linux kernel interface that exposes attestation report generation through the configfs virtual filesystem. Instead of device-specific ioctls (like `/dev/sev-guest`), it provides a uniform API across [Trusted Security Module](#tsm) providers. See the kernel ABI documentation (`Documentation/ABI/testing/configfs-tsm`) for the full interface.

For SEV-SNP guests, configfs-tsm is provided by the `tsm_report` kernel module (`tsm` on older kernels), and the [sev-guest driver](#sev-guest-driver) registers as its SEV-SNP provider. The workflow is:

1. **Mount configfs** (if not already mounted): `mount -t configfs none /sys/kernel/config`
2. **Create a report directory**: `mkdir /sys/kernel/config/tsm/report/<name>`. Each directory is independent and has its own `generation` counter, so several reports can coexist.
3. **Write request data**: Write a nonce (up to 64 bytes) to `inblob`. This becomes the report's `REPORT_DATA` field.
4. **Track generation**: The `generation` file starts at 0 and increments on every write to `inblob` or another attribute (e.g. `privlevel`).
5. **Read the report**: Reading `outblob` generates the attestation report and returns it as binary data. The kernel caches it and regenerates only after `generation` changes.

The report has the same structure and signing as one from `snpguest report`. configfs-tsm is a different interface to the same firmware command (`SNP_GUEST_REQUEST` / `MSG_REPORT_REQ`), but it requests VMPL 0 by default (`privlevel`), whereas `snpguest report` defaults to VMPL 1.

### Key Components

| Component | Purpose |
|---|---|
| **`/sys/kernel/config/tsm/report/`** | Root directory for attestation report requests |
| **`<name>/`** | User-created directory representing one report request |
| **`<name>/inblob`** | Write-only file accepting the request data (nonce, up to 64 bytes) |
| **`<name>/outblob`** | Read-only file returning the generated attestation report |
| **`<name>/generation`** | Read-only counter that increments on every write to `inblob` or another attribute |

---

## How To Use It

### Prerequisites

The guest kernel must have:
- **TSM Reports** enabled: `CONFIG_TSM_REPORTS=y` or `CONFIG_TSM_REPORTS=m`
- **SEV-guest driver** enabled and working (`snpguest ok` succeeds)
- **configfs** filesystem support

### Basic Usage

```sh
# 1. Mount configfs if not already mounted
mount -t configfs none /sys/kernel/config
ls /sys/kernel/config/tsm  # Should show the tsm directory

# 2. Create a report request directory
mkdir /sys/kernel/config/tsm/report/my_report

# 3. Check initial generation (should be 0)
cat /sys/kernel/config/tsm/report/my_report/generation

# 4. Write a nonce (kept in a file so it can be checked later)
dd if=/dev/urandom of=/root/nonce.bin bs=64 count=1 status=none
cat /root/nonce.bin > /sys/kernel/config/tsm/report/my_report/inblob

# 5. Verify generation incremented to 1
cat /sys/kernel/config/tsm/report/my_report/generation

# 6. Read the attestation report (this generates it)
cat /sys/kernel/config/tsm/report/my_report/outblob > /root/attestation_report.bin

# 7. (Optional) Display the report with snpguest
snpguest display report /root/attestation_report.bin
```

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
   - Write a 64-byte random nonce (saved as `configfs_tsm_attestation_report_nonce.bin`) to `inblob`
   - Verify `generation` incremented to exactly 1
   - Read `outblob` to generate the report binary
   - Pull the report (`configfs_tsm_attestation_report.bin`) and the nonce to the host

**4. Attestation Workflow Verification (Required)**
   - Display the report with `snpguest display report`
   - Fetch ARK/ASK certificates from KDS
   - Fetch the VCEK certificate for this report from KDS
   - Verify the certificate chain (VCEK signed by ASK, ASK signed by ARK)
   - Verify the report signature against the VCEK and that `REPORT_DATA` equals the nonce, with `snpguest verify attestation --report-data`

**5. Reported TCB Check (Required)**
   - Lower the host's Reported TCB by reducing Boot Loader by 1 via `snphost config set`
   - Save the modified host TCB to `host_tcb_v2.json`
   - Verify the host's Reported TCB reflects the change while Platform TCB remains unchanged
   - Generate a second report from the **same live guest** via configfs-tsm (this shows that a running guest picks up a Reported TCB change made after it launched):
     - Create `/sys/kernel/config/tsm/report/reported_tcb_check`
     - Verify generation tracking (0 → 1)
     - Write nonce, read outblob, pull `reported_tcb_check.bin` and the nonce
   - Display the new report as JSON
   - Verify its certificate chain, signature and `REPORT_DATA`
   - **Assert** the new report's Reported TCB matches `host_tcb_v2.json` Reported TCB
   - Stop the VM (no longer needed)

**6. Cleanup**
   - Reset the host TCB with `snphost config reset`
   - Verify restoration with `snphost ok` (Reported TCB == Platform TCB)

### What is Checked

| Check | Validates |
|---|---|
| **TSM module presence** | Kernel has TSM support (`tsm` or `tsm_report` module, or built-in) |
| **sev-guest driver** | SEV-SNP guest firmware interface is working |
| **configfs mount** | configfs filesystem is available and tsm tree is accessible |
| **Generation tracking** | A fresh report directory starts at generation 0 and is exactly 1 after one `inblob` write |
| **Report generation** | Reading `outblob` produces a binary attestation report |
| **Certificate chain** | VCEK chains to ASK and ARK (`snpguest verify certs`) |
| **Report signature** | Signed by the fetched VCEK, and the report's TCB matches the VCEK certificate (`snpguest verify attestation`) |
| **REPORT_DATA** | The report's `REPORT_DATA` equals the nonce written to `inblob` |
| **Reported TCB reflection** | A live guest's new report reflects host TCB changes made via `snphost config set` |
| **TCB restoration** | `snphost config reset` restores Reported TCB to Platform TCB |

### Severities

- **Setup steps**: Baseline TCB save, VM launch, enablement checks, first report generation. A failure skips the rest of the test.
- **Required steps**: Attestation workflow, TCB modification, second report generation, TCB check, cleanup. A failure is reported but subsequent steps still run.
- **Info steps**: VM stop. Logged but does not affect pass/fail.

### Artifacts

All artifacts land in `artifacts/3.0/3.0.0-2/configfs_tsm/`:

| File | Contents |
|---|---|
| `host_tcb_v1.json` | Original host TCB before any changes |
| `host_tcb_v2.json` | Host TCB after Boot Loader lowered by 1 |
| `configfs_tsm_attestation_report.bin` | First attestation report (baseline) |
| `configfs_tsm_attestation_report_nonce.bin` | Nonce written to `inblob` for the first report |
| `configfs_tsm_attestation_report.log` | Human-readable display of first report |
| `reported_tcb_check.bin` | Second report (after TCB change) |
| `reported_tcb_check_nonce.bin` | Nonce written to `inblob` for the second report |
| `reported_tcb_check.json` | JSON display of second report |
| `certs/` | ARK, ASK, VCEK for first report |
| `certs_reported_tcb/` | ARK, ASK, VCEK for second report |

With `--debug`, the `configfs-tsm-vm/` directory contains QEMU logs.

### What is not covered

- **Extended report requests** (certificates via `auxblob`) are not tested; certificates come from KDS.
- **Multiple writes to `inblob`** in one directory are not tested (only the 0 to 1 transition).
- **Report size limits** and malformed input handling are not tested.
- **TDX or other TSM providers** are not covered; the test is SEV-SNP specific.
- **`CURRENT_TCB` and `LAUNCH_TCB`** are not checked; the test only checks `REPORTED_TCB`.

---

## Known Quirks

- **Generation counter is per directory.** Removing and recreating the directory resets it to 0. The test uses a fresh directory per report.
- **Module name varies by kernel version.** Older kernels use `tsm`, newer ones use `tsm_report`. The test checks for both, and also accepts `CONFIG_TSM_REPORTS=y` (built-in, no module).
- **No sudo in guest.** The guest image runs the agent as root, so `sudo` is not used in guest commands. If testing on a non-root guest, prefix the configfs commands with `sudo`.
- **snphost `-o json` requires a recent build.** The test uses `snphost show tcb -o json` and will fail with older builds. Set `$SNPHOST` to point to a newer binary if the system `snphost` lacks JSON output support.
- **snpguest `-o json` requires v0.11.0 or later.** The test runs `snpguest display report -o json` on the host; v0.10.0 and earlier fail with `unexpected argument '-o' found`. Set `$SNPGUEST` to a newer binary if the system `snpguest` is older (for example, when it is pinned to v0.10.0 for the ID block test).

---

## Glossary

<a id="configfs"></a>
**configfs** — A Linux virtual filesystem (`CONFIG_CONFIGFS_FS`) that exposes kernel subsystem configuration as a filesystem hierarchy. Users create directories and write files to configure subsystems. For TSM, configfs provides the `/sys/kernel/config/tsm/report/` interface.

<a id="generation"></a>
**generation** — A per-report-directory counter. Starts at 0 and increments on every write to `inblob` or another attribute (e.g. `privlevel`). Lets userspace detect a concurrent writer between its write and its `outblob` read.

<a id="inblob"></a>
**inblob** — The write-only file in a configfs-tsm report directory. Writing a nonce (up to 64 bytes) here sets the request data and bumps `generation`. The nonce becomes the report's `REPORT_DATA` field.

<a id="outblob"></a>
**outblob** — The read-only file in a configfs-tsm report directory. Reading it generates and returns the binary attestation report. The kernel caches the result and regenerates it only after `generation` changes.

<a id="reported-tcb"></a>
**Reported TCB** — The TCB version the host reports via `snphost show tcb`. It determines which [VCEK](#vcek) signs attestation reports. Can be lowered via `snphost config set` for VCEK management (the change is in-memory only and resets on reboot). See the [TCB Config & Commit](tcb-config-commit.md) feature page.

<a id="sev-guest-driver"></a>
**sev-guest driver** — The Linux kernel driver (`/dev/sev-guest`, `CONFIG_SEV_GUEST`) that interfaces with the SEV-SNP firmware. It provides both the ioctl interface and the TSM provider backend for configfs-tsm.

<a id="tsm"></a>
**TSM (Trusted Security Module)** — A Linux kernel framework that provides a uniform interface for attestation across different confidential computing technologies (SEV-SNP, TDX, etc.). Each technology provides a TSM "provider" that implements the framework's callbacks.

<a id="vcek"></a>
**VCEK (Versioned Chip Endorsement Key)** — An attestation signing key derived from chip-unique secrets and a TCB version. The VCEK corresponding to a report's `REPORTED_TCB` signs that report. Verifiers fetch the matching VCEK certificate from AMD's [Key Distribution Service (KDS)](#kds) to validate the signature. See the [TCB Config & Commit](tcb-config-commit.md#vcek) feature page.

<a id="kds"></a>
**KDS (Key Distribution Service)** — AMD's public service (`kdsintf.amd.com`) that distributes VCEK certificates. Given a chip ID and TCB version, it returns the corresponding VCEK certificate. See the [TCB Config & Commit](tcb-config-commit.md#kds) feature page.
