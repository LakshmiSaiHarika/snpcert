# sev_verify

Host-side testing harness for SEV-SNP certification. Reads TOML manifests that declare which tests to run, imports per-test Python modules that define executable steps, and orchestrates execution across host and guest environments.
sev-verify uses a non-secure vsock channel between the host and the guest, which is launched as a CVM. Given the purpose of sev-certify, this is acceptable. The vsock channel properties are properties of the guest and the guest is purpose-built for sev-certify. As built, there is no incentive for an attacker to take advantage of the security weakness of the vsock channel.

## Usage

```bash
# Run a specific certification level
python3 -m sev_verify /path/to/guest.efi -v 3.0

# Run multiple levels
python3 -m sev_verify /path/to/guest.efi -v 3.0 -v 3.1

# Override QEMU and/or OVMF (paths must exist; applied to every test that launches a VM)
python3 -m sev_verify /path/to/guest.efi --qemu-binary /opt/qemu/bin/qemu-system-x86_64 --ovmf /usr/share/ovmf/OVMF.amdsev.fd -v 3.0
# Short form for QEMU:
python3 -m sev_verify /path/to/guest.efi --qemu /opt/qemu/bin/qemu-system-x86_64

# Run all certifications found in cert_tests/
python3 -m sev_verify /path/to/guest.efi

# Put per-test artifacts somewhere other than ./artifacts
python3 -m sev_verify /path/to/guest.efi --artifacts-dir /data/sev-artifacts -v 3.0

# Put results somewhere other than results/
python3 -m sev_verify /path/to/guest.efi --output-dir /data/sev-artifacts -v 3.0

# Enable debug logging (steps.log, guest logs, QEMU boot logs)
python3 -m sev_verify /path/to/guest.efi --debug -v 3.0
```

## How it works

1. Discover manifests at `cert_tests/*/manifest.toml`. Each manifest declares test entries (name, scope, module path).

2. For each test, import its Python module and call `steps()` to get the ordered list of **`BaseStep`** records. Each has a **`kind`** field (`host`, `guest`, `vm_launch`, …). Define steps with **`Step`** either **chained** (``Step(...).host(command=...)``, …) or **in one call** with ``Step.for_host(...)``, ``Step.for_callable(...)``, etc., so your editor shows every required parameter for that shape. Only the fields relevant to ``kind`` may be set; invalid combinations are rejected at construction.

   - **`type`** — certification semantics: `setup` (failure skips remaining steps), `required`, or `info`.
   - **`kind`** — what runs: `host`, `vm_launch`, **`vm_stop`**, `guest`, `guest_pull`, or **`callable`** (in-process handler on the test module; see below).
   - Common fields on **`Step`**: **`expected_result`** (default ``exit_code:0``), **`timeout`** (default **10** seconds); kind-specific arguments go on the chained method or the matching ``Step.for_*`` factory.

3. **Callable steps** — ``Step(...).call(handler="fn")`` or ``Step.for_callable(..., handler="fn")`` builds a step whose `kind` is `callable`. The harness calls `getattr(<test_module>, step.handler)(ctx)` where **`ctx`** is a **`StepContext`**: manifest **`test`**, CLI **`guest_path`**, **`step_results`** from earlier steps in this run, the loaded **`module`**, when a VM is active **`profile`** / **`launch`**, and global **`cli_qemu_binary`** / **`cli_ovmf_path`** when you passed **`--qemu-binary`** / **`--ovmf`**. The handler must return **`StepHandlerResult(exit_code=..., stdout=..., stderr=...)`**; the same **`expected_result`** rules apply (`exit_code:…`, `stdout_contains:…`). Use this for comparisons (e.g. parse `report.bin` and check fields), derived checks, or any logic that is not a shell one-liner. The step **`timeout`** is enforced with a thread-pool wait (stuck CPU in C extensions may not interrupt cleanly).

4. If in the test manifest the scope is defined as either `guest` or `mixed`, the harness builds a `VMProfile` from the test module (`vm_profile()` or `vm_profile` attribute) merged with the CLI `path_to_guest`. If `vm_profile` is omitted, defaults from `vm_profile.VMProfile` are used with the CLI image.

5. Execute steps in order. A `vm_launch` step starts QEMU and waits for the vsock agent; `guest` / `guest_pull` communicate with the VM that has been launched. A **`vm_stop`** step calls `stop_vm` and clears the running guest; Later `vm_launch` can start again. Host steps use subprocess. `guest_pull` runs `base64` on the guest and writes decoded bytes to `host_dest`. If the test ends with a guest still running, the harness still tears it down in a `finally` block.

6. Write results to `results/` (or ``--output-dir``).

## Artifacts directory

Per-test files (pulled guest binaries, logs you add, etc.) go under **``--artifacts-dir``** (default `./artifacts`), organized as::

    <artifacts-dir>/<manifest version>/<test level>/<test_name>/

Example: manifest ``version = "3.0"``, test ``name = "vm-launch-attest"``, ``level = "3.0.0-0"`` → ``artifacts/3.0/3.0.0-0/vm_launch_attest/`` (hyphens in the manifest name become underscores in the folder name).

Prerequisite tests (no certification) use ``<artifacts-dir>/prereqs/<test_name>/``.

The harness creates the directory before the first step and prints ``Artifacts: …``. Callable steps use ``ctx.artifact_dir``; host shell steps get ``$SEV_VERIFY_ARTIFACT_DIR``. For ``guest_pull``, a *relative* ``host_dest`` is resolved under ``artifact_dir``; absolute paths are unchanged.

## Debug logging

When ``--debug`` is enabled, the harness creates detailed logs for debugging test execution and guest behavior. These are written to the test's artifact directory:

**Test-level logs:**
- ``steps.log`` — Step-by-step execution log with commands, exit codes, stdout/stderr, and timing

**Per-guest logs** (under ``guest_<id>/``):
- ``qemu-command.log`` — Full QEMU command line used to launch the guest
- ``qemu-boot.log`` — Guest serial console output (kernel dmesg logs from boot through shutdown)
- ``qemu-error.log`` — QEMU stderr output for debugging launch failures
- ``guest-journal.log`` — Guest journald logs pulled via vsock before VM stop

The boot log is written at both ``vm_launch`` (to capture logs if the guest crashes during boot) and ``vm_stop`` (to capture the complete dmesg including shutdown). The journal log is fetched via vsock just before stopping the VM.

Example artifact structure with ``--debug``:
```
artifacts/3.0/3.0.0-0/attestation_test/
├── steps.log
├── guest_4c64c664-d4da-4e23-a891-f2bde43676ae/
│   ├── steps.log
│   ├── qemu-command.log
│   ├── qemu-boot.log
│   ├── qemu-error.log
│   └── guest-journal.log
└── report.bin
```

## Layout

```
sev_verify/              Harness package
  cli.py                 CLI arg parsing + entry point
  models.py              Step (factory), BaseStep (runtime record), TestDefinition, …
  runner.py              load_test_execution_plan, run_step, run_vm_launch_step, …
  vm_profile.py          VMProfile, QEMU argv, vm_launch / stop_vm
  guest_vsock.py         vsock command channel to the guest
  step_log.py            Debug logging for steps and guest artifacts
  cert_tests/            Certification levels
    common/              Shared test modules
      snp_ok.py      Example host-only test
      ...
    c3_0/                Level 3.0 (example)
      manifest.toml      What to run
      ...
results/                 Output (gitignored)
```

## Requirements

Python 3.11+ (uses `tomllib` from stdlib). No external packages.
