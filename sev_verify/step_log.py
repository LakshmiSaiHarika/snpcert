"""Step execution logging for debugging."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import BaseStep, StepResult


class StepLogger:
    """Writes step execution details to the artifact directory."""

    def __init__(self, artifact_dir: Path) -> None:
        self.log_path = artifact_dir / "steps.log"

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def log_step(
        self, step: BaseStep, result: StepResult, *, command: str | None = None
    ) -> None:
        with open(self.log_path, "a") as f:
            f.write(f"Step: {step.name}\n")
            f.write(f"Timestamp: {self._ts()}\n")
            if step.kind in ("host", "guest"):
                f.write(f"Command: {step.command}\n")
            elif step.kind == "callable":
                f.write(f"Handler: {step.handler}\n")
                if result.command:
                    f.write(f"Command: {result.command}\n")
            elif step.kind == "guest_pull":
                f.write(f"Pull: {step.guest_src} -> {step.host_dest}\n")
                if result.command:
                    f.write(f"Command to read the guest file {step.guest_src}: {result.command}\n")
            elif step.kind == "vm_launch" and command:
                f.write(f"Command: {command}\n")
            elif step.kind == "vm_stop" and result.command:
                f.write(f"Command: {result.command}\n")
            else:
                f.write(f"Kind: {step.kind}\n")
            f.write(f"Duration: {result.duration_ms}ms\n")
            f.write(f"Status: {result.result.upper()}")
            if result.exit_code is not None:
                f.write(f" (exit={result.exit_code})")
            f.write("\n")
            if result.stdout:
                f.write(f"[stdout]\n{result.stdout}")
                if not result.stdout.endswith("\n"):
                    f.write("\n")
            if result.stderr:
                f.write(f"[stderr]\n{result.stderr}")
                if not result.stderr.endswith("\n"):
                    f.write("\n")
            f.write("-" * 60 + "\n")
