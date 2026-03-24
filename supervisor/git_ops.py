"""Git operations on the artifacts repository."""
import asyncio
import os
from pathlib import Path

import structlog

log = structlog.get_logger()


def artifacts_root() -> str:
    return os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts")


class GitError(Exception):
    pass


async def git(*args: str) -> str:
    """Run a git command in the artifacts repo. Returns stdout."""
    cwd = artifacts_root()
    # Uses create_subprocess_exec (not shell=True) — no injection risk
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def promote(generation: int, artifact_path: Path) -> str:
    """Commit artifact on a branch, merge to main, tag as gen-N. Returns tag name."""
    branch = f"gen-{generation}"
    tag = f"gen-{generation}"

    # Branch was already created and committed in spawn_handler — just switch to main
    await git("checkout", "main")
    await git("merge", branch, "--no-ff", "-m", f"Promote generation {generation}")
    await git("tag", "-a", tag, "-m", f"Generation {generation} promoted")
    await git("branch", "-d", branch)

    log.info("generation_promoted", generation=generation, tag=tag)
    return tag


async def rollback(generation: int) -> str:
    """Tag failed artifact, delete branch. Returns tag name."""
    branch = f"gen-{generation}"
    base_tag = f"gen-{generation}-failed"

    # Handle retry suffixes if the failed tag already exists
    existing_tags = (await git("tag", "-l", f"{base_tag}*")).splitlines()
    if existing_tags:
        tag = f"{base_tag}-{len(existing_tags) + 1}"
    else:
        tag = base_tag

    # Tag before deleting the branch (branch may not exist if build failed before commit)
    try:
        await git("tag", "-a", tag, branch, "-m", f"Generation {generation} failed")
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
    current = await git("rev-parse", "--abbrev-ref", "HEAD")
    if current != "main":
        await git("checkout", "main")
