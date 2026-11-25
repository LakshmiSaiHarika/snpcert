"""configfs-tsm: attest through configfs-tsm, then check the Reported TCB.

Mixed-scope test (needs ``--allow-host-changes``): a guest generates a report
through ``/sys/kernel/config/tsm/report``, the host verifies its certificate
chain, signature and REPORT_DATA, then lowers the host Reported TCB, has the
same guest generate a second report and checks it carries the lowered value.
Finally the host config is reset and ``snphost ok`` confirms it is back in line.

The JSON output (``-o json``) needs a recent snphost and snpguest 0.11.0 or
later. Set ``$SNPHOST`` / ``$SNPGUEST`` to use binaries that are not on ``PATH``::

    SNPHOST=/path/to/snphost SNPGUEST=/path/to/snpguest \\
        python3 -m sev_verify path/to/guest.efi -v 3.0.0-2 --allow-host-changes

Under sudo, put the assignment after ``sudo`` (``sudo SNPHOST=... python3 ...``);
sudo drops variables exported in the calling shell.

See docs/features/configfs-tsm.md for the full flow.
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

_TSM_REPORT_ROOT = "/sys/kernel/config/tsm/report"

# configfs-tsm report entries (directory names under _TSM_REPORT_ROOT). Each is
# freshly created, so it has its own generation counter. Files named after an
# entry: <entry>.bin (report), <entry>.json / <entry>.log (display) and
# <entry>_nonce.bin (REPORT_DATA), on the guest under /root and in the artifact dir.
_ATTEST_ENTRY = "configfs_tsm_attestation_report"
_TCB_CHECK_ENTRY = "reported_tcb_check"

# Host artifact file names (under $SEV_VERIFY_ARTIFACT_DIR / ctx.artifact_dir).
_TCB_V1 = "host_tcb_v1.json"
_TCB_V2 = "host_tcb_v2.json"
_TCB_CHECK_JSON = f"{_TCB_CHECK_ENTRY}.json"

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
    """snphost binary: ``$SNPHOST`` if set, else ``snphost`` on ``PATH``.

    Every snphost invocation in this test (host and callable steps) goes
    through here, so the whole run uses one binary and steps.log records it.
    """
    return os.environ.get("SNPHOST") or "snphost"


def _snphost_cmd(*args: str) -> str:
    """Host step command line running the resolved snphost with ``args``."""
    return shlex.join([_snphost(), *args])


def _snpguest() -> str:
    """Host snpguest binary: ``$SNPGUEST`` if set, else ``snpguest`` on ``PATH``.

    Only host steps use it; the guest image ships its own snpguest. Returned
    shell-quoted because the command lines embed ``$SEV_VERIFY_ARTIFACT_DIR``
    and so are built as shell strings, not via ``shlex.join``.
    """
    return shlex.quote(os.environ.get("SNPGUEST") or "snpguest")


def _save_host_tcb(filename: str) -> str:
    """Host command saving ``snphost show tcb -o json`` to an artifact file."""
    path = f'"$SEV_VERIFY_ARTIFACT_DIR/{filename}"'
    return f"{_snphost_cmd('show', 'tcb', '-o', 'json')} > {path} && cat {path}"


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


# ── configfs-tsm report generation ──────────────────────────────


def _configfs_tsm_report_steps(entry: str, step_type: str) -> list[BaseStep]:
    """Generate a report in configfs-tsm entry `entry` and pull it.

    Pulls ``<entry>.bin`` (the report) and ``<entry>_nonce.bin`` (the 64-byte
    nonce written to ``inblob``, i.e. the expected REPORT_DATA) to the artifact dir.
    """
    entry_dir = f"{_TSM_REPORT_ROOT}/{entry}"
    guest_report = f"/root/{entry}.bin"
    guest_nonce = f"/root/{entry}_nonce.bin"
    # The entry is freshly created, so generation must be 0, and exactly 1
    # after one inblob write. A non-zero guest exit code fails the step.
    return [
        Step.for_guest(
            name=f"{entry}: create entry",
            type=step_type,
            command=f"mkdir {entry_dir}",
            timeout=30,
        ),
        Step.for_guest(
            name=f"{entry}: original generation is 0",
            type=step_type,
            command=f'g=$(cat {entry_dir}/generation); echo "generation $g"; [ "$g" -eq 0 ]',
            timeout=30,
        ),
        # The 64-byte nonce becomes REPORT_DATA. No sudo: the guest agent
        # already runs as root and the image has no sudo.
        Step.for_guest(
            name=f"{entry}: write nonce to inblob",
            type=step_type,
            command=(
                f"dd if=/dev/urandom of={guest_nonce} bs=64 count=1 status=none"
                f" && cat {guest_nonce} > {entry_dir}/inblob"
            ),
            timeout=30,
        ),
        Step.for_guest(
            name=f"{entry}: generation advanced to 1",
            type=step_type,
            command=f'g=$(cat {entry_dir}/generation); echo "generation $g"; [ "$g" -eq 1 ]',
            timeout=30,
        ),
        # Reading outblob generates the report.
        Step.for_guest(
            name=f"{entry}: read outblob",
            type=step_type,
            command=f"cat {entry_dir}/outblob > {guest_report}",
            timeout=60,
        ),
        Step.for_guest_pull(
            name=f"{entry}: pull report",
            type=step_type,
            guest_src=guest_report,
            host_dest=f"{entry}.bin",
            timeout=120,
        ),
        Step.for_guest_pull(
            name=f"{entry}: pull nonce",
            type=step_type,
            guest_src=guest_nonce,
            host_dest=f"{entry}_nonce.bin",
            timeout=120,
        ),
    ]


# ── Attestation workflow ────────────────────────────────────────


def _verify_report_steps(entry: str, certs_dir: str) -> list[BaseStep]:
    """Fetch ARK/ASK and the VCEK from KDS; verify chain, signature and REPORT_DATA."""
    report_path = f'"$SEV_VERIFY_ARTIFACT_DIR/{entry}.bin"'
    nonce_path = f'"$SEV_VERIFY_ARTIFACT_DIR/{entry}_nonce.bin"'
    certs_path = f'"$SEV_VERIFY_ARTIFACT_DIR/{certs_dir}"'
    return [
        Step.for_host(
            name=f"{entry}: fetch ca",
            type="required",
            command=f"{_snpguest()} fetch ca pem {certs_path} -r {report_path}",
            timeout=60,
        ),
        Step.for_host(
            name=f"{entry}: fetch vcek",
            type="required",
            command=f"{_snpguest()} fetch vcek pem {certs_path} {report_path}",
            timeout=60,
        ).add_hint("429", "Rate limited by KDS, re-run in a minute"),
        Step.for_host(
            name=f"{entry}: verify certs",
            type="required",
            command=f"{_snpguest()} verify certs {certs_path}",
            timeout=60,
        ),
        # verify certs only checks ARK -> ASK -> VCEK. This checks the report's
        # signature against the VCEK and that REPORT_DATA equals the nonce.
        # --report-data needs the 0x prefix; without it the value is taken as 64 raw ASCII bytes.
        Step.for_host(
            name=f"{entry}: verify report signature and REPORT_DATA",
            type="required",
            command=(
                f"{_snpguest()} verify attestation {certs_path} {report_path}"
                f" --report-data 0x$(od -An -v -tx1 {nonce_path} | tr -d ' \\n')"
            ),
            timeout=60,
        ),
    ]


# ── reported_tcb_check ──────────────────────────────────────────


def config_set_lower_bootloader(ctx: StepContext) -> StepHandlerResult:
    """Set Reported TCB to v1 Platform TCB with Boot Loader - 1 (``snphost config set``)."""
    try:
        tcb = _load_host_tcb(ctx, _TCB_V1)["platform_tcb"]
    except ValueError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    try:
        bl = int(tcb["bootloader"])
        tee = int(tcb["tee"])
        snp = int(tcb["snp_fw"])
        ucode = int(tcb["microcode"])
        fmc = tcb.get("fmc")
    except KeyError as e:
        return StepHandlerResult(exit_code=1, stderr=f"Missing TCB field in {_TCB_V1}: {e}")
    except (ValueError, TypeError) as e:
        return StepHandlerResult(exit_code=1, stderr=f"Invalid TCB value in {_TCB_V1}: {e}")

    if bl == 0:
        return StepHandlerResult(
            exit_code=1,
            stderr="Cannot run test: Platform Boot Loader TCB is 0; nothing to lower.",
        )

    # Positional args: bootloader tee snp-fw microcode mask-chip [fmc].
    # FMC is null on pre-Turin parts and must be omitted from the args list.
    args = [_snphost(), "config", "set", str(bl - 1), str(tee), str(snp), str(ucode), "0"]
    if fmc is not None:
        args.append(str(int(fmc)))

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return StepHandlerResult(
            exit_code=1,
            stderr="snphost config set timed out after 30s",
            command=shlex.join(args),
        )

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
        guest = _load_json(ctx, _TCB_CHECK_JSON, ("reported_tcb",))["reported_tcb"]
    except ValueError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    errors = [
        f"FAIL: {host_f}: host {host.get(host_f)} != report {guest.get(guest_f)}"
        for host_f, guest_f in _TCB_FIELDS.items()
        if host.get(host_f) != guest.get(guest_f)
    ]
    dump = [
        f"  host Reported ({_TCB_V2}):   {host}",
        f"  report Reported ({_TCB_CHECK_JSON}): {guest}",
    ]
    if errors:
        return StepHandlerResult(exit_code=1, stderr="\n".join(errors + dump))
    return StepHandlerResult(
        exit_code=0,
        stdout="\n".join(["Guest report Reported TCB matches host Reported TCB"] + dump),
    )


# ── Step definitions ────────────────────────────────────────────


def steps() -> list[BaseStep]:
    """
    Steps to attest through configfs-tsm and check Reported TCB

    1. Save current host TCB to host_tcb_v1.json
    2. Launch the SNP-enabled guest
    3. Check enablement: TSM report module, sev-guest driver, configfs mount
    4. Generate a configfs-tsm report; pull it and its nonce
    5. Display the report, fetch ARK/ASK and VCEK from KDS, verify the chain, signature and REPORT_DATA
    6. Reported TCB check: lower the host Boot Loader TCB by 1, save host_tcb_v2.json,
       generate and verify a second report via configfs-tsm, compare its Reported TCB
       with the host's
    7. Reset the host TCB config and do snphost ok.
    """
    return [
        # 1. Collect Host TCB before any change
        Step.for_host(
            name="show-tcb-v1",
            type="setup",
            command=_save_host_tcb(_TCB_V1),
        ).add_hint(
            "unexpected argument",
            "snphost does not support '-o json'; set $SNPHOST to a newer snphost build",
        ),
        # 2. Launch SNP-enabled guest
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
        # 3. Enablement checks. TSM report module: tsm (older kernels) or
        #    tsm_report (newer); built-in (=y) shows no module, so accept that too.
        Step.for_guest(
            name="tsm-module-present",
            type="setup",
            command=(
                r"lsmod | grep -E '^(tsm|tsm_report)\b'"
                f" || {_GUEST_KCONFIG} | grep -x 'CONFIG_TSM_REPORTS=y'"
            ),
            timeout=30,
        ),
        Step.for_guest(
            name="snpguest-ok",
            type="setup",
            command="snpguest ok",
            timeout=60,
        ),
        # Mount configfs (idempotent: succeeds if already mounted)
        Step.for_guest(
            name="mount-configfs",
            type="setup",
            command=(
                "mount -t configfs none /sys/kernel/config 2>/dev/null || true;"
                " ls /sys/kernel/config/tsm"
            ),
            timeout=30,
        ),
        # 4. Generate the baseline report. `setup`: the host is untouched so
        #    far, and a generation mismatch fails the whole test.
        *_configfs_tsm_report_steps(_ATTEST_ENTRY, "setup"),
        # 5. Save and verify the baseline report.
        Step.for_host(
            name=f"{_ATTEST_ENTRY}: display report",
            type="required",
            command=(
                f'{_snpguest()} display report "$SEV_VERIFY_ARTIFACT_DIR/{_ATTEST_ENTRY}.bin"'
                f' > "$SEV_VERIFY_ARTIFACT_DIR/{_ATTEST_ENTRY}.log"'
            ),
            timeout=30,
        ),
        *_verify_report_steps(_ATTEST_ENTRY, "certs"),
        # 6. Reported TCB check: lower the host Boot Loader TCB by 1.
        Step.for_callable(
            name="config-set-lower-bootloader",
            type="required",
            handler="config_set_lower_bootloader",
            timeout=60,
        ),
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
        # Second report from the same live guest, so it shows a mid-life
        # Reported TCB change being picked up.
        *_configfs_tsm_report_steps(_TCB_CHECK_ENTRY, "required"),
        Step.for_host(
            name=f"{_TCB_CHECK_ENTRY}: display report json",
            type="required",
            command=(
                f'{_snpguest()} display report -o json "$SEV_VERIFY_ARTIFACT_DIR/{_TCB_CHECK_ENTRY}.bin"'
                f' > "$SEV_VERIFY_ARTIFACT_DIR/{_TCB_CHECK_JSON}"'
            ),
            timeout=30,
        ).add_hint(
            "unexpected argument",
            "snpguest does not support '-o json' (needs 0.11.0+); set $SNPGUEST to a newer snpguest",
        ),
        *_verify_report_steps(_TCB_CHECK_ENTRY, "certs_reported_tcb"),
        Step.for_callable(
            name="reported-tcb-check",
            type="required",
            handler="reported_tcb_check",
            timeout=30,
        ),
        # Guest no longer needed after all verification completes.
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
        # 7. Restore TCB and confirm Reported TCB is back in line.
        Step.for_host(
            name="config-reset",
            type="required",
            command=_snphost_cmd("config", "reset"),
        ),
        Step.for_host(
            name="snphost-ok",
            type="required",
            command=_snphost_cmd("ok"),
            timeout=60,
        ).add_hint(
            "TCB versions did NOT match",
            "Reported TCB still differs from Platform TCB; run: "
            + _snphost_cmd("config", "reset"),
        ),
    ]
