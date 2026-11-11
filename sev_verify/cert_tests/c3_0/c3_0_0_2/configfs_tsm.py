"""configfs-tsm: Attestation through configfs-tsm, and a Reported TCB check.

Mixed-scope test:

1. On host, save the original host TCB in ``host_tcb_v1.json`` via snphost show tcb -o json``.
2. Launch SNP-enabled guest.
3. On the guest, perform enablement checks — check for TSM report module, sev-guest driver, and if configfs filesystem is mounted.
4. On the guest, generate an Attestation Report through ``/sys/kernel/config/tsm/report``. 
   Prior to the generation of an Attestation Report, track the report generation, which should be 0 initially.
   Write a `nonce` data to the inblob, then check if the report generation is exactly 1 higher than the previous report generation.
   Read ``outblob`` to generate the Attestation report via configfs-tsm, and then pull the Attestation Report Report into the host.
5. On the host,save the report and fetch ARK/ASK/VCEK from KDS and verify the certificate chain.
6. Then, perform Reported TCB check, where we:
   track the original host TCB in ``host_tcb_v1.json``, 
   then modify the host TCB by reducing only Boot Loader by 1 via ``snphost config set` & save the modified host TCB in ``host_tcb_v2.json``,
   later generate a second new attestation report via configfs-tsm module from the same guest and verify its certificate chain, 
   and check if the Attestation Report's `Reported TCB` matches the host's.
7. Then revert the host TCB to its original value and perform ``snphost ok`` to see if Reported TCB is back in line.

The JSON output (``-o json``) needs a recent snphost. Set ``$SNPHOST`` to use a
binary that is not on ``PATH``::

    SNPHOST=/path/to/snphost python3 -m sev_verify GUEST.EFI -v 3.0.0-2 \\
        --allow-host-changes
"""

import json
import os
import shlex
import subprocess

from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=4096,
)

_SNPHOST_SHELL = '"${SNPHOST:-snphost}"'

_TSM_REPORT_ROOT = "/sys/kernel/config/tsm/report"

# configfs-tsm report entries (directory names under _TSM_REPORT_ROOT).
_ATTEST_ENTRY = "configfs_tsm_attestation_report"
_TCB_CHECK_ENTRY = "reported_tcb_check"

# Report file names: /root/<name> on the guest, <name> in the artifact dir.
_ATTEST_REPORT = "configfs_tsm_attestation_report.bin"
_TCB_CHECK_REPORT = "attestation_report_reported_tcb_test.bin"
_TCB_CHECK_REPORT_JSON = "attestation_report_reported_tcb_test.json"

# Host artifact file names (under $SEV_VERIFY_ARTIFACT_DIR / ctx.artifact_dir).
_TCB_V1 = "host_tcb_v1.json"
_TCB_V2 = "host_tcb_v2.json"

# Running kernel's config on the guest: /boot first, /proc/config.gz fallback.
_GUEST_KCONFIG = '{ cat "/boot/config-$(uname -r)" 2>/dev/null || zcat /proc/config.gz 2>/dev/null; }'

# snphost ``show tcb -o json`` field -> snpguest ``display report -o json`` field.
# fmc is null on pre-Turin parts.
_TCB_FIELDS = {
    "bootloader": "bootloader",
    "tee": "tee",
    "snp_fw": "snp",
    "microcode": "microcode",
    "fmc": "fmc",
}


# ── Helpers ─────────────────────────────────────────────────────


def _snphost() -> str:
    """snphost binary for callable steps (same lookup as host steps)."""
    return os.environ.get("SNPHOST") or "snphost"


def _save_host_tcb(filename: str) -> str:
    """Host command saving ``snphost show tcb -o json`` to an artifact file.
    """
    path = f'"$SEV_VERIFY_ARTIFACT_DIR/{filename}"'
    return f"{_SNPHOST_SHELL} show tcb -o json > {path} && cat {path}"


def _load_json(ctx: StepContext, filename: str, sections: tuple[str, ...]) -> dict:
    """Load an artifact JSON file and require the given dict sections.

    Raises:
        ValueError: file missing, malformed, or lacking a section.
    """
    path = ctx.artifact_dir / filename
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read {path}: {e}") from e
    for section in sections:
        if not isinstance(data.get(section), dict):
            raise ValueError(f"no {section} in {path}")
    return data


def _load_host_tcb(ctx: StepContext, filename: str) -> dict[str, dict]:
    """Load a saved ``snphost show tcb -o json`` file."""
    return _load_json(ctx, filename, ("reported_tcb", "platform_tcb"))


def _step_stdout(ctx: StepContext, name: str) -> str | None:
    """Stdout of an earlier step in this test, or None if it did not run."""
    for sr in ctx.step_results:
        if sr.step.name == name:
            return sr.stdout
    return None


# ── configfs-tsm report generation ──────────────────────────────


def _original_generation_step(entry: str) -> str:
    return f"{entry}: original generation"


def _updated_generation_step(entry: str) -> str:
    return f"{entry}: updated generation"


def _read_generation(ctx: StepContext, step_name: str) -> int:
    """Parse the generation counter printed by an earlier guest step."""
    out = _step_stdout(ctx, step_name)
    try:
        return int((out or "").strip())
    except ValueError:
        raise ValueError(f"cannot parse generation from {step_name!r}: {out!r}") from None


def _verify_original_generation(ctx: StepContext, entry: str) -> StepHandlerResult:
    """A fresh report entry must start at generation 0 (no user data yet)."""
    try:
        original = _read_generation(ctx, _original_generation_step(entry))
    except ValueError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))
    if original != 0:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"FAIL: {entry} original generation is {original}, expected 0",
        )
    return StepHandlerResult(exit_code=0, stdout=f"{entry} original generation is 0")


def _verify_updated_generation(ctx: StepContext, entry: str) -> StepHandlerResult:
    """Writing inblob must bump generation by exactly 1."""
    try:
        original = _read_generation(ctx, _original_generation_step(entry))
        updated = _read_generation(ctx, _updated_generation_step(entry))
    except ValueError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))
    if updated != original + 1:
        return StepHandlerResult(
            exit_code=1,
            stderr=(
                f"FAIL: {entry} generation {original} -> {updated} after inblob "
                f"write, expected {original + 1}"
            ),
        )
    return StepHandlerResult(
        exit_code=0,
        stdout=f"{entry} generation {original} -> {updated}",
    )


# The harness calls handlers by name with ctx only, hence one wrapper per entry.
def verify_attest_original_generation(ctx: StepContext) -> StepHandlerResult:
    return _verify_original_generation(ctx, _ATTEST_ENTRY)


def verify_attest_updated_generation(ctx: StepContext) -> StepHandlerResult:
    return _verify_updated_generation(ctx, _ATTEST_ENTRY)


def verify_tcb_check_original_generation(ctx: StepContext) -> StepHandlerResult:
    return _verify_original_generation(ctx, _TCB_CHECK_ENTRY)


def verify_tcb_check_updated_generation(ctx: StepContext) -> StepHandlerResult:
    return _verify_updated_generation(ctx, _TCB_CHECK_ENTRY)


def _configfs_tsm_report_steps(
    entry: str,
    report: str,
    step_type: str,
    verify_original: str,
    verify_updated: str,
) -> list[BaseStep]:
    """Generate an Attestation Report report via configfs-tsm module in `entry` directory and pull it.

    The report lands at ``/root/<report>`` on the guest and ``<report>`` in the
    artifact dir. ``verify_original`` / ``verify_updated`` name this entry's
    generation-check handlers.
    """
    entry_dir = f"{_TSM_REPORT_ROOT}/{entry}"
    guest_report = f"/root/{report}"
    return [
        Step.for_guest(
            name=f"{entry}: create Attestation Report entry",
            type=step_type,
            command=f"mkdir {entry_dir}",
            timeout=30,
        ),
        Step.for_guest(
            name=_original_generation_step(entry),
            type=step_type,
            command=f"cat {entry_dir}/generation",
            timeout=30,
        ),
        Step.for_callable(
            name=f"{entry}: verify original generation is 0",
            type=step_type,
            handler=verify_original,
            timeout=30,
        ),
        # 64-byte nonce becomes the report's REPORT_DATA. No sudo: the guest
        # agent already runs as root and the image has no sudo. tee's stdout
        # goes to /dev/null: the agent decodes output as text, and raw nonce
        # bytes would break its response. tee's exit status masks a dd
        # failure, but the generation check after this step catches it.
        Step.for_guest(
            name=f"{entry}: write nonce to inblob",
            type=step_type,
            command=(
                f"cd {entry_dir} && dd if=/dev/urandom bs=64 count=1"
                " | tee inblob > /dev/null"
            ),
            timeout=30,
        ),
        Step.for_guest(
            name=_updated_generation_step(entry),
            type=step_type,
            command=f"cat {entry_dir}/generation",
            timeout=30,
        ),
        Step.for_callable(
            name=f"{entry}: verify generation advanced by 1",
            type=step_type,
            handler=verify_updated,
            timeout=30,
        ),
        # Reading outblob generates the report.
        Step.for_guest(
            name=f"{entry}: read outblob",
            type=step_type,
            command=f"cat {entry_dir}/outblob > {guest_report}",
            timeout=300,
        ),
        Step.for_guest_pull(
            name=f"{entry}: pull report",
            type=step_type,
            guest_src=guest_report,
            host_dest=report,
            timeout=120,
        ),
    ]


# ── Attestation workflow ────────────────────────────────────────


def _verify_certs_steps(report: str, certs_dir: str) -> list[BaseStep]:
    """Fetch ARK/ASK and the report's VCEK from KDS; verify the chain."""
    report_path = f'"$SEV_VERIFY_ARTIFACT_DIR/{report}"'
    certs_path = f'"$SEV_VERIFY_ARTIFACT_DIR/{certs_dir}"'
    return [
        Step.for_host(
            name=f"{report}: fetch ca",
            type="required",
            command=f"snpguest fetch ca pem {certs_path} -r {report_path}",
            timeout=60,
        ),
        Step.for_host(
            name=f"{report}: fetch vcek",
            type="required",
            command=f"snpguest fetch vcek pem {certs_path} {report_path}",
            timeout=60,
        ).add_hint("429", "Rate limited by KDS, re-run in a minute"),
        Step.for_host(
            name=f"{report}: verify certs",
            type="required",
            command=f"snpguest verify certs {certs_path}",
            timeout=60,
        ),
    ]


# ── reported_tcb_check ──────────────────────────────────────────


def config_set_lower_bootloader(ctx: StepContext) -> StepHandlerResult:
    """Set the Platform TCB from host_tcb_v1.json with Boot Loader lowered by 1."""
    try:
        tcb = _load_host_tcb(ctx, _TCB_V1)["platform_tcb"]
        bl = int(tcb["bootloader"])
        tee = int(tcb["tee"])
        snp = int(tcb["snp_fw"])
        ucode = int(tcb["microcode"])
        fmc = tcb.get("fmc")
    except (ValueError, KeyError, TypeError) as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    if bl == 0:
        return StepHandlerResult(
            exit_code=1,
            stderr="Cannot run test: Platform Boot Loader TCB is 0; nothing to lower.",
        )

    # Positional args: bootloader tee snp-fw microcode mask-chip [fmc].
    # FMC is null on pre-Turin parts and must be omitted there.
    args = [_snphost(), "config", "set", str(bl - 1), str(tee), str(snp), str(ucode), "0"]
    if fmc is not None:
        args.append(str(int(fmc)))

    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    cmd = shlex.join(proc.args)
    if proc.returncode != 0:
        return StepHandlerResult(
            exit_code=1,
            stdout=proc.stdout,
            stderr=f"snphost config set failed: {proc.stderr.strip()}",
            command=cmd,
        )

    return StepHandlerResult(
        exit_code=0,
        stdout=f"Boot Loader TCB lowered {bl} -> {bl - 1}",
        command=cmd,
    )


def verify_reported_tcb_lowered(ctx: StepContext) -> StepHandlerResult:
    """host_tcb_v2.json: Reported = v1 Platform with Boot Loader - 1; Platform unchanged."""
    try:
        v1 = _load_host_tcb(ctx, _TCB_V1)
        v2 = _load_host_tcb(ctx, _TCB_V2)
    except ValueError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    platform_v1 = v1["platform_tcb"]
    expected = dict(platform_v1)
    try:
        expected["bootloader"] = int(platform_v1["bootloader"]) - 1
    except (KeyError, TypeError, ValueError) as e:
        return StepHandlerResult(exit_code=1, stderr=f"bad bootloader in {_TCB_V1}: {e}")

    reported_v2 = v2["reported_tcb"]
    platform_v2 = v2["platform_tcb"]

    errors: list[str] = []
    for f in _TCB_FIELDS:
        if reported_v2.get(f) != expected.get(f):
            errors.append(f"FAIL: Reported {f} = {reported_v2.get(f)}, expected {expected.get(f)}")
        if platform_v2.get(f) != platform_v1.get(f):
            errors.append(f"FAIL: Platform {f} changed {platform_v1.get(f)} -> {platform_v2.get(f)}")

    dump = [
        f"  v1 Platform: {platform_v1}",
        f"  v2 Reported: {reported_v2}",
        f"  v2 Platform: {platform_v2}",
    ]
    if errors:
        return StepHandlerResult(exit_code=1, stderr="\n".join(errors + dump))
    return StepHandlerResult(
        exit_code=0,
        stdout="\n".join(["Host Reported TCB carries the lowered Boot Loader"] + dump),
    )


def reported_tcb_check(ctx: StepContext) -> StepHandlerResult:
    """The new guest report's Reported TCB must match host_tcb_v2.json Reported TCB."""
    try:
        host = _load_host_tcb(ctx, _TCB_V2)["reported_tcb"]
        guest = _load_json(ctx, _TCB_CHECK_REPORT_JSON, ("reported_tcb",))["reported_tcb"]
    except ValueError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    errors = [
        f"FAIL: {host_f}: host {host.get(host_f)} != report {guest.get(guest_f)}"
        for host_f, guest_f in _TCB_FIELDS.items()
        if host.get(host_f) != guest.get(guest_f)
    ]
    dump = [
        f"  host Reported ({_TCB_V2}):   {host}",
        f"  report Reported ({_TCB_CHECK_REPORT_JSON}): {guest}",
    ]
    if errors:
        return StepHandlerResult(exit_code=1, stderr="\n".join(errors + dump))
    return StepHandlerResult(
        exit_code=0,
        stdout="\n".join(["Guest report Reported TCB matches host Reported TCB"] + dump),
    )


# ── Step definitions ────────────────────────────────────────────


def steps() -> list[BaseStep]:
    '''
    Steps to attest through configfs-tsm and check Reported TCB

    1. Save current host TCB to host_tcb_v1.json
    2. Launch the SNP-enabled guest
    3. Perform configfs-tsm enablement checks for tsm module, sev-driver and configfs filesystem
    4. Generate a configfs-tsm report and pull it
    5. Save the Attestation Report, fetch ARK/ASK and VCEK from KDS and verify the certificate chain
    6. Perform Reported TCB check: change the host TCB by reducing the Boot Loader by 1, save
       the host TCB in host_tcb_v2.json, generate and verify a second Attestation Report via configfs-tsm module, compare its
       Reported TCB with the host's.
    7. Reset the host TCB config and do snphost ok.
    '''
    return [
        # Collect Host TCB before any change
        Step.for_host(
            name="show-tcb-v1",
            type="setup",
            command=_save_host_tcb(_TCB_V1),
        ).add_hint(
            "unexpected argument",
            "snphost does not support '-o json'; set $SNPHOST to a newer snphost build",
        ),
        # Launch SNP-enabled guest
        Step.for_vm_launch(
            name="Launch SEV-SNP guest",
            guest_id="configfs-tsm-vm",
            type="setup",
            timeout=300,
        ).add_hint(
            "Address already in use",
            "A previous VM may still be running. "
            "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')",
        ),
        # Check for TSM report module: tsm (older kernels) or tsm_report (newer).
        #     Built-in (=y) shows no module, so accept that too.
        Step.for_guest(
            name="tsm-module-present",
            type="setup",
            command=(
                r"lsmod | grep -E '^(tsm|tsm_report)\b'"
                f" || {_GUEST_KCONFIG} | grep -x 'CONFIG_TSM_REPORTS=y'"
            ),
            timeout=30,
        ),
        # Check for sev-guest driver
        Step.for_guest(
            name="snpguest-ok",
            type="setup",
            command="snpguest ok",
            timeout=60,
        ),
        # Mount configfs unless the tsm tree is already visible
        Step.for_guest(
            name="mount-configfs",
            type="setup",
            command=(
                "if [ ! -d /sys/kernel/config/tsm ]; then"
                " mount -t configfs none /sys/kernel/config; fi;"
                " ls /sys/kernel/config/tsm"
            ),
            timeout=30,
        ),

        # 4. Generate SNP guest attestation report using configfs-tsm module. `setup`: the host is untouched so far,
        #    and a generation mismatch fails the whole test.
        *_configfs_tsm_report_steps(
            _ATTEST_ENTRY,
            _ATTEST_REPORT,
            "setup",
            "verify_attest_original_generation",
            "verify_attest_updated_generation",
        ),
        
        # Save the Attestation Report  
        Step.for_host(
            name=f"{_ATTEST_REPORT}: display report",
            type="required",
            command=(
                f'snpguest display report "$SEV_VERIFY_ARTIFACT_DIR/{_ATTEST_REPORT}"'
                ' > "$SEV_VERIFY_ARTIFACT_DIR/configfs_tsm_attestation_report.log"'
            ),
            timeout=30,
        ),
        
        # Perform Attestation workflow process.
        *_verify_certs_steps(_ATTEST_REPORT, "certs"),
        
        # Perform Reported TCB Check
        # Change the host TCB by reducing the host bootloader by 1
        Step.for_callable(
            name="config-set-lower-bootloader",
            type="required",
            handler="config_set_lower_bootloader",
            timeout=60,
        ),

        # View the Host TCB after the change
        Step.for_host(
            name="show-tcb-v2",
            type="required",
            command=_save_host_tcb(_TCB_V2),
        ),
        Step.for_callable(
            name="verify-host-reported-tcb-lowered",
            type="required",
            handler="verify_reported_tcb_lowered",
            timeout=30,
        ),
        # Generate the second new Attestation Report from the same live guest
        *_configfs_tsm_report_steps(
            _TCB_CHECK_ENTRY,
            _TCB_CHECK_REPORT,
            "required",
            "verify_tcb_check_original_generation",
            "verify_tcb_check_updated_generation",
        ),
        # Guest no longer needed.
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
        # Verify the new Attestation Report
        # Save the new Attestation Report
        Step.for_host(
            name=f"{_TCB_CHECK_REPORT}: display report json",
            type="required",
            command=(
                f'snpguest display report -o json "$SEV_VERIFY_ARTIFACT_DIR/{_TCB_CHECK_REPORT}"'
                f' > "$SEV_VERIFY_ARTIFACT_DIR/{_TCB_CHECK_REPORT_JSON}"'
            ),
            timeout=30,
        ),
        # Perform Attestation workflow for the new Attestation Report
        *_verify_certs_steps(_TCB_CHECK_REPORT, "certs_reported_tcb"),
        
        # Perform Reported TCB Check to validate if Reported TCB components match
        Step.for_callable(
            name="reported-tcb-check",
            type="required",
            handler="reported_tcb_check",
            timeout=30,
        ),
        # 7. Restore TCB and confirm Reported TCB is back in line.
        Step.for_host(
            name="config-reset",
            type="required",
            command=f"{_SNPHOST_SHELL} config reset",
        ),
        Step.for_host(
            name="snphost-ok",
            type="required",
            command=f"{_SNPHOST_SHELL} ok",
            timeout=60,
        ).add_hint(
            "TCB versions did NOT match",
            "Reported TCB still differs from Platform TCB; run: snphost config reset",
        ),
    ]
