"""
QMP (QEMU Machine Protocol) client for VM management.

QMP is QEMU's JSON-RPC-like protocol over Unix sockets. This module
provides synchronous access to QMP commands for graceful shutdown,
VM introspection, and runtime control.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .vm_profile import VMProfile


class QMPError(Exception):
    """Base exception for QMP protocol errors."""


class QMPConnectionError(QMPError):
    """Failed to connect to QMP socket."""


class QMPTimeoutError(QMPError):
    """QMP operation timed out."""


class QMPCommandError(QMPError):
    """QMP command returned an error response."""

    def __init__(
        self,
        message: str,
        error_class: str | None = None,
        error_desc: str | None = None,
    ):
        super().__init__(message)
        self.error_class = error_class
        self.error_desc = error_desc


@dataclass(frozen=True)
class QMPVersion:
    """QEMU version info from QMP greeting."""

    qemu_major: int
    qemu_minor: int
    qemu_micro: int
    package: str = ""


@dataclass(frozen=True)
class QMPStatus:
    """VM status from query-status."""

    running: bool
    status: str


@dataclass(frozen=True)
class QMPCommandResult:
    """Result of a QMP command execution."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None
    error_desc: str | None = None


class QMPClient:
    """Synchronous QMP client for QEMU control.

    Usage:
        with QMPClient("/tmp/qmp.sock") as qmp:
            status = qmp.query_status()
            qmp.system_powerdown()

    Or manually:
        qmp = QMPClient("/tmp/qmp.sock")
        qmp.connect()
        try:
            qmp.quit()
        finally:
            qmp.close()
    """

    def __init__(
        self,
        socket_path: str | Path,
        timeout: float = 10.0,
    ):
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._version: QMPVersion | None = None
        self._connected = False
        self._buffer = b""

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def version(self) -> QMPVersion | None:
        return self._version

    def connect(self) -> QMPVersion:
        """Connect to QMP socket and negotiate capabilities."""
        if self._connected:
            return self._version

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)

        try:
            self._sock.connect(str(self.socket_path))
        except OSError as exc:
            self._sock.close()
            self._sock = None
            raise QMPConnectionError(
                f"Failed to connect to {self.socket_path}: {exc}"
            ) from exc

        greeting = self._recv_response()
        if "QMP" not in greeting:
            self.close()
            raise QMPConnectionError("Invalid QMP greeting")

        ver = greeting["QMP"]["version"]["qemu"]
        self._version = QMPVersion(
            qemu_major=ver["major"],
            qemu_minor=ver["minor"],
            qemu_micro=ver["micro"],
            package=greeting["QMP"]["version"].get("package", ""),
        )

        self._send_command("qmp_capabilities")
        self._connected = True
        return self._version

    def close(self) -> None:
        """Close the QMP connection."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._connected = False
        self._buffer = b""

    def __enter__(self) -> "QMPClient":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _send_command(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send QMP command and return response."""
        if not self._sock:
            raise QMPConnectionError("Not connected")

        payload: dict[str, Any] = {"execute": command}
        if arguments:
            payload["arguments"] = arguments

        data = json.dumps(payload, separators=(",", ":")) + "\n"
        self._sock.sendall(data.encode("utf-8"))

        while True:
            response = self._recv_response()
            if "return" in response or "error" in response:
                break

        if "error" in response:
            err = response["error"]
            raise QMPCommandError(
                f"QMP command {command!r} failed: {err.get('desc', 'unknown')}",
                error_class=err.get("class"),
                error_desc=err.get("desc"),
            )

        return response.get("return", {})

    def _recv_response(self) -> dict[str, Any]:
        """Read one JSON response from socket."""
        while b"\n" not in self._buffer:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout as exc:
                raise QMPTimeoutError("Timed out waiting for QMP response") from exc
            if not chunk:
                raise QMPConnectionError("QMP socket closed unexpectedly")
            self._buffer += chunk

        line, sep, remainder = self._buffer.partition(b"\n")
        self._buffer = remainder
        return json.loads(line.decode("utf-8"))

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> QMPCommandResult:
        """Execute an arbitrary QMP command (returns result rather than raising)."""
        try:
            data = self._send_command(command, arguments)
            return QMPCommandResult(success=True, data=data)
        except QMPCommandError as exc:
            return QMPCommandResult(
                success=False,
                error_class=exc.error_class,
                error_desc=exc.error_desc,
            )

    def query_status(self) -> QMPStatus:
        """Query VM running status."""
        result = self._send_command("query-status")
        return QMPStatus(
            running=result.get("running", False),
            status=result.get("status", "unknown"),
        )

    def query_cpus(self) -> list[dict[str, Any]]:
        """Query CPU information."""
        return self._send_command("query-cpus-fast")

    def query_memory(self) -> dict[str, Any]:
        """Query memory size summary."""
        return self._send_command("query-memory-size-summary")

    def system_powerdown(self) -> None:
        """Request graceful ACPI shutdown (guest must respond)."""
        self._send_command("system_powerdown")

    def quit(self) -> None:
        """Immediately terminate QEMU (no guest cooperation needed)."""
        try:
            self._send_command("quit")
        except QMPConnectionError:
            pass

    def stop(self) -> None:
        """Pause VM execution."""
        self._send_command("stop")

    def cont(self) -> None:
        """Resume VM execution."""
        self._send_command("cont")


def generate_qmp_socket_path(profile: "VMProfile") -> str:
    """Generate unique QMP socket path based on CID."""
    return f"/tmp/sev-verify-qmp-{profile.vsock_cid}.sock"


def wait_for_qmp_socket(
    socket_path: str | Path,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> None:
    """Wait for QMP socket to become available."""
    path = Path(socket_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(poll_interval)
    raise QMPTimeoutError(f"QMP socket {socket_path} not available after {timeout}s")
