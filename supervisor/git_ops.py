"""Git operations on the artifacts repository."""

import asyncio
import os
from pathlib import Path

import structlog

log = structlog.get_logger()

# Single lock serialising all git operations on the artifacts repo.
# Git's own locking prevents concurrent index writes, but we also need to
# prevent interleaved branch checkouts (checkout A / checkout B / commit on B
# leaving A's files committed under B's branch name).
_git_lock: asyncio.Lock = asyncio.Lock()


def artifacts_root() -> str:
    return os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts")


class GitError(Exception):
    pass


async def git(*args: str) -> str:
    """Run a git command in the artifacts repo. Returns stdout.

    Does NOT acquire _git_lock — callers that need atomicity across multiple
    git commands must hold the lock themselves (see create_generation_branch,
    promote, rollback).
    """
    cwd = artifacts_root()
    # Uses create_subprocess_exec (not shell=True) — no injection risk
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def create_generation_branch(generation: int, artifact_path: str = ".") -> None:
    """Checkout main, create gen-N branch, stage artifact files, and commit.

    Called from handle_spawn. Holds _git_lock for the entire sequence to
    prevent concurrent spawns from racing on branch creation and checkout.
    """
    async with _git_lock:
        current = await git("rev-parse", "--abbrev-ref", "HEAD")
        if current != "main":
            await git("checkout", "main")
        await git("checkout", "-b", f"gen-{generation}")
        await git("add", "-A", artifact_path)
        await git(
            "commit",
            "--allow-empty",
            "-m",
            f"Generation {generation} artifact",
        )


async def promote(generation: int, artifact_path: Path) -> str:
    """Commit artifact on a branch, merge to main, tag as gen-N. Returns tag name."""
    branch = f"gen-{generation}"
    tag = f"gen-{generation}"

    async with _git_lock:
        await git("checkout", "main")
        await git("merge", branch, "--no-ff", "-m", f"Promote generation {generation}")
        await git("tag", "-a", tag, "-m", f"Generation {generation} promoted")
        await git("branch", "-d", branch)

    log.info("generation_promoted", generation=generation, tag=tag)
    return tag


async def rollback(generation: int) -> str:
    """Tag failed artifact, delete branch. Returns tag name."""
    branch = f"gen-{generation}"
    tag = f"gen-{generation}-failed"

    async with _git_lock:
        existing = await git("tag", "-l", tag)
        if existing:
            log.warning(
                "rollback_tag_exists",
                tag=tag,
                msg="overwriting; each retry should have a unique generation number",
            )

        try:
            await git("tag", "-fa", tag, branch, "-m", f"Generation {generation} failed")
        except GitError:
            pass

        try:
            await git("branch", "-D", branch)
        except GitError:
            pass

    log.info("generation_rolled_back", generation=generation, tag=tag)
    return tag


async def ensure_on_main() -> None:
    """Make sure the artifacts repo is on main branch."""
    async with _git_lock:
        current = await git("rev-parse", "--abbrev-ref", "HEAD")
        if current != "main":
            await git("checkout", "main")


async def ensure_repo() -> None:
    """Initialize the artifacts repo if it does not already exist.

    Creates the directory, runs git init (with main as default branch),
    and makes an empty initial commit so the repo has a HEAD. Safe to call
    on an already-initialized repo (.git exists -> returns immediately).
    """
    root = Path(artifacts_root())
    root.mkdir(parents=True, exist_ok=True)

    if (root / ".git").exists():
        return

    cwd = str(root)
    for cmd_args in (
        ("init", "-b", "main"),
        ("commit", "--allow-empty", "-m", "Initial commit"),
    ):
        proc = await asyncio.create_subprocess_exec(
            "git",
            *cmd_args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    log.info("artifacts_repo_initialized", path=cwd)
