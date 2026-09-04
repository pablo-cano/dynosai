# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Governed Git inspection, branch, commit, diff, and merge operations."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .db import Database
from .util import decode_git_path, run_command, slugify, utc_now
from .policy import PathPolicyEngine, is_managed_workflow_artifact


class GitError(RuntimeError):
    pass


class GitManager:
    def __init__(self, root: str | Path, db: Database):
        self.root = Path(root).resolve()
        self.db = db

    def _git_executable(self) -> str:
        """Resolve the controller Git binary without trusting the agent PATH.

        Managed agent sessions prepend DynosAI's read-only Git Guard to PATH.
        DynosAI itself is the Git controller and must therefore invoke the real
        executable directly.  The guard metadata is created before the agent is
        launched and records that executable.  Falling back to PATH is safe only
        when it does not resolve back into the project guard directory.
        """
        meta = self.root / ".dynosai" / "runtime" / "git-guard" / "guard.json"
        if meta.exists():
            try:
                candidate = str(json.loads(meta.read_text(encoding="utf-8")).get("real_git") or "").strip()
                if candidate and Path(candidate).exists():
                    return candidate
            except Exception:
                pass
        candidate = shutil.which("git")
        if not candidate:
            raise GitError("git executable not found")
        try:
            guard = (self.root / ".dynosai" / "runtime" / "git-guard").resolve()
            resolved = Path(candidate).resolve()
            if resolved == guard / "git" or guard in resolved.parents:
                raise GitError("real git executable cannot be resolved from guarded PATH")
        except OSError:
            pass
        return candidate

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True):
        try:
            return run_command([self._git_executable(), "-c", "core.quotepath=false", *args], cwd or self.root, check=check)
        except Exception as exc:
            raise GitError(f"git {' '.join(args)} failed: {exc}") from exc

    def is_repo(self) -> bool:
        """True only when this root is the Git toplevel.

        A subdirectory of another repository must not inherit that parent as
        source authority. Nested greenfield fixtures live inside the DynosAI
        checkout; treating them as the parent worktree made
        ``git status`` dirty and blocked ``dynosai_work start``.
        """
        result = self._git("rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0 or not str(result.stdout or "").strip():
            return False
        try:
            top = Path(result.stdout.strip()).resolve()
        except OSError:
            return False
        return top == self.root.resolve()

    def initialize_repo(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.is_repo():
            result = self._git("init", "-b", "main", check=False)
            if result.returncode != 0:
                self._git("init")
                self._git("branch", "-M", "main")
        # Project-local identity avoids forcing global user configuration.
        if self._git("config", "user.email", check=False).returncode != 0:
            self._git("config", "user.email", "dynosai@local")
        if self._git("config", "user.name", check=False).returncode != 0:
            self._git("config", "user.name", "DynosAI")

    def head(self, cwd: Path | None = None) -> str:
        result = self._git("rev-parse", "HEAD", cwd=cwd, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def current_branch(self, cwd: Path | None = None) -> str:
        result = self._git("branch", "--show-current", cwd=cwd, check=False)
        return result.stdout.strip() or "main"

    def status(self, cwd: Path | None = None) -> list[str]:
        result = self._git("status", "--porcelain", "--untracked-files=all", cwd=cwd, check=False)
        return [line for line in result.stdout.splitlines() if line.strip()]

    @staticmethod
    def _status_path(line: str) -> str:
        raw = line[3:] if len(line) > 3 else line
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        return decode_git_path(raw).rstrip("/")

    @staticmethod
    def _is_merge_noise(rel: str) -> bool:
        path = decode_git_path(rel).rstrip("/")
        if not path or path == ".":
            return True
        if is_managed_workflow_artifact(path):
            return True
        parts = path.split("/")
        if parts[0] in {".dynosai", ".cursor", ".codex", ".agents", ".claude", ".specify"}:
            return True
        if any(part in {"__pycache__", ".pytest_cache", ".venv"} or part.endswith(".pyc") for part in parts):
            return True
        return False

    def merge_blockers(self, cwd: Path | None = None) -> list[str]:
        """Return leftover source paths that must be committed or discarded before merge."""
        seen: list[str] = []
        for line in self.status(cwd):
            rel = self._status_path(line)
            if self._is_merge_noise(rel) or rel in seen:
                continue
            seen.append(rel)
        return seen

    def ensure_initial_commit(self, message: str = "chore: initialize DynosAI project") -> str:
        if self.head():
            return self.head()
        self._git("add", "-A")
        result = self._git("diff", "--cached", "--quiet", check=False)
        if result.returncode == 0:
            marker = self.root / ".dynosai" / ".initialized"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(utc_now() + "\n", encoding="utf-8")
            self._git("add", "-A")
        self._git("commit", "-m", message)
        return self.head()


    def commit_main_changes(self, message: str) -> str:
        """Commit DynosAI-owned metadata only; never absorb user source changes."""
        managed = [
            ".dynosai/config.toml", ".dynosai/provider-models.toml", ".dynosai/agent-profile.json", ".dynosai/state", ".gitignore", ".specify", "specs",
            "docs/architecture", "AGENTS.md", "CLAUDE.md", ".mcp.json", ".claude",
            ".cursor", ".codex", ".agents",
        ]
        for rel in managed:
            path = self.root / rel
            # -A on an explicit path records both managed additions and deletions.
            if path.exists() or self._git("ls-files", "--error-unmatch", rel, check=False).returncode == 0:
                self._git("add", "-A", "--", rel, check=False)
        changed = self._git("diff", "--cached", "--quiet", check=False)
        if changed.returncode != 0:
            self._git("commit", "-m", message)
        return self.head()

    def worktree_base(self) -> Path:
        configured = self.db.get_meta("worktree_base")
        if configured:
            return Path(configured)
        base = self.root.parent / ".dynosai-worktrees" / self.root.name
        self.db.set_meta("worktree_base", str(base))
        return base


    def create_interactive_branch(self, work_id: str, title: str, *, require_clean: bool = True) -> str:
        """Create the feature branch in the already-open project workspace.

        This is the provider-native strategy for Cursor/Codex: the IDE stays on the
        same filesystem path while DynosAI changes only the Git branch under the
        trusted controller.
        """
        if require_clean and self.status(self.root):
            raise GitError("Interactive workspace must be clean before starting a feature")
        main_branch = self.db.get_meta("main_branch", self.current_branch()) or "main"
        current = self.current_branch(self.root)
        if current != main_branch:
            raise GitError(f"Interactive workspace must start on {main_branch}; current branch is {current}")
        branch = f"dyn/{work_id.lower()}-{slugify(title)}"
        exists = self._git("show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
        if exists:
            self._git("switch", branch)
        else:
            self._git("switch", "-c", branch, main_branch)
        self.db.audit("GitInteractiveBranchCreated", work_id, {"branch": branch, "workspace": str(self.root), "main_branch": main_branch})
        return branch

    def cleanup_interactive_branch(self, branch: str) -> None:
        main_branch = self.db.get_meta("main_branch", "main") or "main"
        if self.current_branch(self.root) != main_branch:
            self._git("switch", main_branch, cwd=self.root)
        self._git("branch", "-D", branch, cwd=self.root, check=False)

    def create_worktree(self, work_id: str, title: str) -> tuple[str, Path]:
        branch = f"dyn/{work_id.lower()}-{slugify(title)}"
        target = self.worktree_base() / work_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            valid = self._git("rev-parse", "--is-inside-work-tree", cwd=target, check=False)
            if valid.returncode == 0 and valid.stdout.strip() == "true":
                return branch, target
            # A previous project copy or interrupted run may leave a stale directory.
            import shutil
            self._git("worktree", "prune", check=False)
            shutil.rmtree(target, ignore_errors=True)
        base_branch = self.db.get_meta("main_branch", self.current_branch()) or "main"
        result = self._git("show-ref", "--verify", f"refs/heads/{branch}", check=False)
        if result.returncode == 0:
            self._git("worktree", "add", str(target), branch)
        else:
            self._git("worktree", "add", "-b", branch, str(target), base_branch)
        self.db.audit("GitWorktreeCreated", work_id, {"branch": branch, "path": str(target)})
        return branch, target


    def create_task_worktree(self, work_id: str, task_id: str, feature_branch: str, feature_worktree: Path) -> tuple[str, Path, str]:
        """Create an isolated task branch/worktree from the current feature branch head."""
        base_commit=self.head(feature_worktree)
        if not base_commit: raise GitError("Feature worktree has no HEAD")
        safe_task=slugify(task_id)
        branch=f"dyn-task/{safe_task}"
        # Task worktrees must never live inside the feature worktree; nested
        # worktrees appear as untracked directories in the parent and break integration.
        target=self.worktree_base().parent/f"{self.root.name}-task-worktrees"/work_id/task_id
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            import shutil
            self._git("worktree","prune",check=False); shutil.rmtree(target,ignore_errors=True)
        if self._git("show-ref","--verify",f"refs/heads/{branch}",check=False).returncode==0:
            self._git("branch","-D",branch,check=False)
        self._git("worktree","add","-b",branch,str(target),base_commit)
        self.db.audit("GitTaskWorktreeCreated",task_id,{"work_id":work_id,"branch":branch,"path":str(target),"base_commit":base_commit,"feature_branch":feature_branch})
        return branch,target,base_commit

    def integrate_task_commit(self, feature_worktree: Path, commit: str) -> str:
        """Cherry-pick a verified task checkpoint into the feature worktree."""
        if self.status(feature_worktree): raise GitError("Feature worktree is dirty before task integration")
        result=self._git("cherry-pick",commit,cwd=feature_worktree,check=False)
        if result.returncode!=0:
            self._git("cherry-pick","--abort",cwd=feature_worktree,check=False)
            raise GitError(f"Task integration conflict for {commit}: {result.stderr.strip()}")
        return self.head(feature_worktree)

    def cleanup_task_worktree(self, branch: str, worktree: Path) -> None:
        self._git("worktree","remove","--force",str(worktree),check=False)
        self._git("branch","-D",branch,check=False)

    def diff_files(self, cwd: Path, base_commit: str | None = None) -> list[str]:
        base = base_commit or self.db.get_meta("main_branch", "main") or "main"
        result = self._git("diff", "--name-only", f"{base}...HEAD", cwd=cwd, check=False)
        files = {decode_git_path(line) for line in result.stdout.splitlines() if line.strip()}
        # Include uncommitted work as agent results are registered before the checkpoint commit.
        unstaged = self._git("diff", "--name-only", cwd=cwd, check=False)
        cached = self._git("diff", "--cached", "--name-only", cwd=cwd, check=False)
        untracked = self._git("ls-files", "--others", "--exclude-standard", cwd=cwd, check=False)
        for output in (unstaged.stdout, cached.stdout, untracked.stdout):
            files.update(decode_git_path(line) for line in output.splitlines() if line.strip())
        return sorted(files)

    def diff_name_status(self, cwd: Path, base_commit: str | None = None) -> dict[str,str]:
        base=base_commit or self.db.get_meta("main_branch","main") or "main"
        statuses:dict[str,str]={}
        committed=self._git("diff","--name-status",f"{base}...HEAD",cwd=cwd,check=False)
        working=self._git("diff","--name-status",cwd=cwd,check=False)
        cached=self._git("diff","--cached","--name-status",cwd=cwd,check=False)
        for output in (committed.stdout,working.stdout,cached.stdout):
            for line in output.splitlines():
                parts=line.split("\t")
                if len(parts)>=2: statuses[decode_git_path(parts[-1])]=parts[0][0]
        untracked=self._git("ls-files","--others","--exclude-standard",cwd=cwd,check=False)
        for rel in untracked.stdout.splitlines():
            if rel.strip(): statuses[decode_git_path(rel)]="A"
        return statuses

    def diff_hunks(self,cwd:Path,base_commit:str|None=None)->dict[str,list[tuple[int,int]]]:
        import re
        base=base_commit or self.db.get_meta("main_branch","main") or "main"
        text=self._git("diff","--unified=0",f"{base}",cwd=cwd,check=False).stdout
        result:dict[str,list[tuple[int,int]]]={}; current=None
        for line in text.splitlines():
            if line.startswith("+++ b/"): current=decode_git_path(line[6:]); result.setdefault(current,[])
            elif current and line.startswith("@@"):
                m=re.search(r"\+(\d+)(?:,(\d+))?",line)
                if m:
                    start=int(m.group(1)); count=int(m.group(2) or "1"); result[current].append((start,max(start,start+max(1,count)-1)))
        return result

    def diff_text(self, cwd: Path, base_commit: str | None = None, max_chars: int = 30000) -> str:
        base = base_commit or self.db.get_meta("main_branch", "main") or "main"
        committed = self._git("diff", f"{base}...HEAD", cwd=cwd, check=False).stdout
        working = self._git("diff", cwd=cwd, check=False).stdout
        cached = self._git("diff", "--cached", cwd=cwd, check=False).stdout
        text = "\n".join(part for part in (committed, working, cached) if part)
        return text[-max_chars:]


    def policy_filtered_diff(self, cwd: Path, base_commit: str | None = None, max_chars: int = 30000) -> dict[str, object]:
        """Return diff content only for paths allowed by central PathPolicy."""
        base = base_commit or self.db.get_meta("main_branch", "main") or "main"
        policy = PathPolicyEngine(cwd)
        allowed=[]; denied=[]; chunks=[]
        for rel in self.diff_files(cwd, base_commit):
            decision=policy.decision(rel,"read",agent=True)
            if not decision.allowed:
                denied.append({"path":rel,"reason":decision.reason}); continue
            allowed.append(rel)
            tracked=self._git("ls-files","--error-unmatch","--",rel,cwd=cwd,check=False).returncode==0
            if tracked:
                text=self._git("diff",base,"--",rel,cwd=cwd,check=False).stdout
                work=self._git("diff","--",rel,cwd=cwd,check=False).stdout
                cached=self._git("diff","--cached","--",rel,cwd=cwd,check=False).stdout
                chunk="\n".join(x for x in (text,work,cached) if x)
            else:
                path=policy.require(rel,"read",agent=True)
                try: body=path.read_text(encoding="utf-8")
                except UnicodeDecodeError: body="<binary file>"
                chunk=f"--- /dev/null\n+++ b/{rel}\n"+body
            if chunk: chunks.append(chunk)
        combined="\n".join(chunks)
        if len(combined)>max_chars: combined=combined[-max_chars:]
        return {"base_commit":base,"files":allowed,"denied":denied,"diff":combined,"truncated":sum(len(x) for x in chunks)>max_chars}

    def changed_against(self, cwd: Path, base_commit: str) -> list[str]:
        return self.diff_files(cwd, base_commit)

    def commit_management_worktree(self, worktree: Path, paths: list[str], message: str) -> str:
        # Unstaged source work may already exist when an agent is connected late.
        # That is safe because we stage only exact DynosAI-generated paths. Pre-staged
        # user source is not safe: committing it would absorb work we do not own.
        staged={decode_git_path(x) for x in self._git("diff","--cached","--name-only",cwd=worktree,check=False).stdout.splitlines() if x.strip()}
        managed=set(paths)
        unexpected_staged=staged-managed
        if unexpected_staged:
            raise GitError(f"Worktree has pre-staged unmanaged changes before agent launch: {sorted(unexpected_staged)}")
        for rel in paths:
            self._git("add","-A","--",rel,cwd=worktree,check=False)
        changed=self._git("diff","--cached","--quiet",cwd=worktree,check=False)
        if changed.returncode!=0:self._git("commit","-m",message,cwd=worktree)
        return self.head(worktree)

    def checkpoint(self, work_id: str, worktree: Path, message: str, extra_paths: list[str] | None = None) -> str:
        # Stage only agent-authorized source changes. DynosAI/provider metadata is never
        # swept into a feature commit by `git add -A`. extra_paths are verified run files
        # that must not be missed if `diff_files` omitted them.
        policy=PathPolicyEngine(worktree)
        paths={decode_git_path(rel) for rel in self.diff_files(worktree) if rel}
        for rel in extra_paths or []:
            decoded=decode_git_path(rel)
            if decoded:
                paths.add(decoded)
        for rel in sorted(paths):
            if is_managed_workflow_artifact(rel):
                continue
            decision=policy.decision(rel,"write",agent=True)
            if decision.allowed:
                self._git("add","-A","--",rel,cwd=worktree,check=False)
        changed=self._git("diff","--cached","--quiet",cwd=worktree,check=False)
        if changed.returncode!=0: self._git("commit","-m",message,cwd=worktree)
        commit=self.head(worktree)
        self.db.execute("INSERT INTO checkpoints(work_id,commit_hash,message,created_at) VALUES(?,?,?,?)",(work_id,commit,message,utc_now()))
        self.db.audit("GitCheckpointCreated",work_id,{"commit":commit,"message":message})
        return commit

    def rollback(self, work_id: str, worktree: Path, commit: str | None = None) -> str:
        if not commit:
            row = self.db.one(
                "SELECT commit_hash FROM checkpoints WHERE work_id=? ORDER BY id DESC LIMIT 1",
                (work_id,),
            )
            if not row:
                raise GitError(f"No checkpoint exists for {work_id}")
            commit = str(row["commit_hash"])
        self._git("reset", "--hard", commit, cwd=worktree)
        self._git("clean", "-fd", cwd=worktree)
        self.db.audit("GitRollback", work_id, {"commit": commit})
        return commit

    def merge(self, work_id: str, branch: str, title: str) -> str:
        blockers = self.merge_blockers()
        if blockers:
            raise GitError(
                "The main workspace has unmanaged changes; run dynosai sync before merging: "
                + ", ".join(blockers)
            )
        main_branch = self.db.get_meta("main_branch", self.current_branch()) or "main"
        self._git("checkout", main_branch)
        self._git("merge", "--squash", branch)
        self._git("commit", "-m", f"feat({work_id}): {title}")
        commit = self.head()
        self.db.audit("GitMerged", work_id, {"branch": branch, "commit": commit})
        return commit

    def cleanup_worktree(self, branch: str, worktree: Path) -> None:
        self._git("worktree", "remove", "--force", str(worktree), check=False)
        self._git("branch", "-D", branch, check=False)
        try:
            if worktree.exists() and not any(worktree.iterdir()):
                os.rmdir(worktree)
        except OSError:
            pass
