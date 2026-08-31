# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider-neutral ExecutionRuntime: brain/hands/session boundary.

The local runtime remains the default. Docker, VMs and remote executors are
future implementations of the same interface. Provider transports (ACP,
app-server) must not leak into this boundary.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .policy import (
    DependencyDecision,
    DependencyPolicy,
    NetworkDecision,
    NetworkPolicy,
    PathPolicyEngine,
    PolicyError,
)
from .provider_process import spawn_provider_process, stop_provider_process
from .secrets import SecretBroker, redact_text, redact_value
from .util import captured_run


@dataclass(slots=True)
class ProcessResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    cwd: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "cwd": self.cwd,
        }


class ExecutionRuntime(Protocol):
    """Hands: filesystem, process and policy-gated operations.

    Brain (provider/orchestration) and Session (durable workflow state) stay
    outside this protocol. Implementations must enforce policy themselves;
    the model cannot authorize a denied action.
    """

    name: str

    def allowed_roots(self) -> list[Path]: ...
    def resolve_path(self, path: str, operation: str = "read", *, agent: bool = True) -> Path: ...
    def read_text(self, path: str, *, max_bytes: int = 1_000_000) -> str: ...
    def write_text(self, path: str, content: str) -> Path: ...
    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: int = 120, env: dict[str, str] | None = None) -> ProcessResult: ...
    def spawn_provider(self, command: list[str], **kwargs: Any) -> subprocess.Popen: ...
    def stop_provider(self, proc: subprocess.Popen, **kwargs: Any) -> None: ...
    def network_decision(self, host: str, port: int | None = None) -> NetworkDecision: ...
    def dependency_decision(self, argv: list[str]) -> DependencyDecision: ...
    def redact(self, value: Any) -> Any: ...


class LocalExecutionRuntime:
    """Default hands implementation: the developer's local workspace.

    Isolation is path/process policy, not a container. Future runtimes can
    wrap the same calls with worktree, container, VM or remote backends.
    """

    name = "local"

    def __init__(
        self,
        root: str | Path,
        *,
        extra_roots: list[Path] | None = None,
        network: NetworkPolicy | None = None,
        dependencies: DependencyPolicy | None = None,
        secrets: SecretBroker | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        conventional = [
            self.root.parent / ".dynosai-worktrees" / self.root.name,
            self.root.parent / f"{self.root.name}-task-worktrees",
        ]
        self._extra_roots = [
            Path(item).expanduser().resolve()
            for item in (*(extra_roots or ()), *conventional)
        ]
        self.path_policy = PathPolicyEngine(self.root)
        self.network = network or NetworkPolicy()
        self.dependencies = dependencies or DependencyPolicy()
        self.secrets = secrets or SecretBroker()

    def allowed_roots(self) -> list[Path]:
        roots = [self.root, *self._extra_roots]
        seen: list[Path] = []
        for item in roots:
            if item not in seen:
                seen.append(item)
        return seen

    def _cwd_allowed(self, workdir: Path) -> bool:
        for root in self.allowed_roots():
            if workdir == root or root in workdir.parents:
                return True
        return False

    def resolve_path(self, path: str, operation: str = "read", *, agent: bool = True) -> Path:
        return self.path_policy.require(path, operation, agent=agent)

    def read_text(self, path: str, *, max_bytes: int = 1_000_000) -> str:
        target = self.resolve_path(path, "read", agent=True)
        data = target.read_bytes()
        if len(data) > max_bytes:
            raise PolicyError(f"read denied for {path}: exceeds max_bytes={max_bytes}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n")
        return redact_text(text)

    def write_text(self, path: str, content: str) -> Path:
        target = self.resolve_path(path, "write", agent=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> ProcessResult:
        if not argv:
            raise PolicyError("process denied: empty command")
        decision = self.dependencies.decide(list(argv))
        if not decision.allowed:
            raise PolicyError(f"process denied: {decision.reason}")
        workdir = Path(cwd or self.root).resolve()
        if not self._cwd_allowed(workdir):
            raise PolicyError("process denied: cwd escapes allowed roots")
        timed_out = False
        try:
            completed = captured_run(
                list(argv),
                cwd=str(workdir),
                capture_output=True,
                check=False,
                timeout=timeout,
                env=env,
            )
            code = int(completed.returncode)
            stdout = redact_text(completed.stdout or "")
            stderr = redact_text(completed.stderr or "")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            code = -1
            stdout = redact_text((exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, (bytes, bytearray)) else str(exc.stdout or ""))
            stderr = redact_text((exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, (bytes, bytearray)) else str(exc.stderr or ""))
        return ProcessResult(list(argv), code, stdout, stderr, timed_out, str(workdir))

    def spawn_provider(self, command: list[str], **kwargs: Any) -> subprocess.Popen:
        if not command:
            raise PolicyError("provider spawn denied: empty command")
        return spawn_provider_process(command, **kwargs)

    def stop_provider(self, proc: subprocess.Popen, **kwargs: Any) -> None:
        notify = kwargs.get("notify")
        if notify is not None and not callable(notify):
            raise TypeError("notify must be callable")
        stop_provider_process(proc, **kwargs)

    def network_decision(self, host: str, port: int | None = None) -> NetworkDecision:
        return self.network.decide(host, port)

    def dependency_decision(self, argv: list[str]) -> DependencyDecision:
        return self.dependencies.decide(argv)

    def redact(self, value: Any) -> Any:
        return redact_value(value)


def default_runtime(root: str | Path) -> LocalExecutionRuntime:
    return LocalExecutionRuntime(root)


# Keep a stable alias for tests and adapters that want an explicit factory.
RuntimeFactory = Callable[[str | Path], ExecutionRuntime]
