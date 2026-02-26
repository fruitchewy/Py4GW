#!/usr/bin/env python3
"""
sync_forks.py — Pull fork-exclusive content and reconcile shared touchpoints.

Usage:
    python sync_forks.py [--dry-run] [--no-claude] [--fork <name>] [--manifest <path>] [--repo <path>]

Requires: git, claude (Claude Code CLI — only for touchpoint reconciliation)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def log(msg):
    print(f"[sync] {msg}")


def warn(msg):
    print(f"[sync] WARNING: {msg}", file=sys.stderr)


def die(msg):
    print(f"[sync] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args, check=True, capture=False, cwd=None):
    """Run a git command. Returns CompletedProcess."""
    cmd = ["git"] + list(args)
    kwargs = {"cwd": cwd or REPO_ROOT}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    if not check:
        kwargs["check"] = False
    else:
        kwargs["check"] = True
    return subprocess.run(cmd, **kwargs)


def git_output(*args, cwd=None):
    """Run a git command and return stdout, or None on failure."""
    result = git(*args, check=False, capture=True, cwd=cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def ensure_remote(name, url):
    existing = git_output("remote", "get-url", name)
    if existing is None:
        log(f"Adding remote: {name} -> {url}")
        if not DRY_RUN:
            git("remote", "add", name, url)


def fetch_remote(remote, branch):
    log(f"Fetching {remote}/{branch} ...")
    if DRY_RUN:
        return
    for attempt in range(4):
        result = git("fetch", remote, branch, check=False, capture=True)
        if result.returncode == 0:
            return
        wait = 2 ** (attempt + 1)
        warn(f"Fetch failed (attempt {attempt + 1}/4), retrying in {wait}s ...")
        time.sleep(wait)
    die(f"Failed to fetch {remote}/{branch} after 4 attempts")


def checkout_paths(ref, paths):
    """Check out paths from ref. Returns list of paths that were actually pulled."""
    pulled = []
    for path in paths:
        # Check if path exists in the ref (try recursive first, then top-level for dirs)
        found = False
        for ls_args in [("ls-tree", "--name-only", "-r", ref, "--", path),
                        ("ls-tree", "--name-only", ref, "--", path)]:
            check = git_output(*ls_args)
            if check is not None and check != "":
                found = True
                break
        if not found:
            warn(f"  Path not found in {ref}: {path} (skipping)")
            continue

        log(f"  Pulling: {path}")
        if DRY_RUN:
            pulled.append(path)
            continue

        # Ensure parent directory exists
        full = Path(REPO_ROOT) / path
        if path.endswith("/"):
            full.mkdir(parents=True, exist_ok=True)
        else:
            full.parent.mkdir(parents=True, exist_ok=True)

        result = git("checkout", ref, "--", path, check=False, capture=True)
        if result.returncode == 0:
            pulled.append(path)
        else:
            warn(f"  git checkout failed for {path}: {result.stderr.strip()}")

    return pulled


def find_claude():
    """Find the claude CLI executable."""
    # Try plain 'claude' first
    if shutil.which("claude"):
        return "claude"
    # Windows: try common npm global locations
    if sys.platform == "win32":
        for candidate in [
            Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
            Path(os.environ.get("LOCALAPPDATA", "")) / "npm" / "claude.cmd",
        ]:
            if candidate.exists():
                return str(candidate)
    return None


def run_claude(prompt, system_prompt_file, allowed_tools, max_turns, timeout_minutes=5):
    """Invoke Claude Code CLI for touchpoint reconciliation.

    Returns True only if Claude ran and git status shows staged changes.
    Returns False on any error, timeout, or if Claude made no changes.
    """
    claude_cmd = find_claude()
    if not claude_cmd:
        return False

    # --allowedTools takes each tool as a separate argument, not comma-separated
    cmd = [
        claude_cmd, "-p", prompt,
        "--append-system-prompt-file", str(system_prompt_file),
        "--max-turns", str(max_turns),
        "--output-format", "text",
        "--verbose",
    ]
    for tool in allowed_tools:
        cmd.extend(["--allowedTools", tool])

    log("Invoking Claude Code for touchpoint reconciliation ...")
    log(f"  Timeout: {timeout_minutes} minutes")
    log(f"  Allowed tools: {allowed_tools}")

    try:
        result = subprocess.run(
            cmd, cwd=REPO_ROOT,
            timeout=timeout_minutes * 60,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        warn(f"Claude Code timed out after {timeout_minutes} minutes")
        return False

    # Print Claude's output so the user can see what happened
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    # Check for failure indicators
    all_output = (result.stdout or "") + (result.stderr or "")
    if "Reached max turns" in all_output:
        warn(f"Claude Code hit max turns ({max_turns}) — likely did not finish")
        return False

    if result.returncode != 0:
        warn(f"Claude Code exited with code {result.returncode}")
        return False

    # Verify Claude actually staged something
    staged = git("diff", "--cached", "--quiet", check=False, capture=True)
    if staged.returncode == 0:
        log("Claude Code ran but made no changes (may be correct if touchpoints are already in sync)")
    else:
        log("Claude Code reconciliation complete — changes staged")

    return True


# ---------------------------------------------------------------------------
# Globals set by main()
# ---------------------------------------------------------------------------
REPO_ROOT = None
DRY_RUN = False


def main():
    global REPO_ROOT, DRY_RUN

    parser = argparse.ArgumentParser(
        description="Pull fork-exclusive content and reconcile shared touchpoints."
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    parser.add_argument("--no-claude", action="store_true", help="Skip Claude Code, print diffs for manual review")
    parser.add_argument("--fork", dest="fork_filter", default="", help="Only process this fork name")
    parser.add_argument("--manifest", default=None, help="Path to fork_manifest.json")
    parser.add_argument("--repo", default=None, help="Path to the Py4GW git repo")
    args = parser.parse_args()

    DRY_RUN = args.dry_run
    script_dir = Path(__file__).resolve().parent

    # Resolve manifest
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else script_dir / "fork_manifest.json"
    if not manifest_path.exists():
        die(f"Manifest not found: {manifest_path}")

    # Resolve repo root
    if args.repo:
        REPO_ROOT = str(Path(args.repo).expanduser().resolve())
    else:
        # Try from script location
        result = git_output("rev-parse", "--show-toplevel", cwd=str(script_dir))
        if result:
            REPO_ROOT = result
    if not REPO_ROOT:
        # Try from cwd
        result = git_output("rev-parse", "--show-toplevel", cwd=os.getcwd())
        if result:
            REPO_ROOT = result
    if not REPO_ROOT:
        die("Could not find git repo. Use --repo <path> or run from inside the repo.")
    if not Path(REPO_ROOT).is_dir():
        die(f"Repo path does not exist: {REPO_ROOT}")

    log(f"Repo root: {REPO_ROOT}")

    # Load manifest
    with open(manifest_path) as f:
        manifest = json.load(f)

    # Read sync options
    opts = manifest.get("sync_options", {})
    auto_commit = opts.get("auto_commit", True)
    commit_prefix = opts.get("commit_prefix", "[fork-sync]")
    use_claude = opts.get("use_claude_for_touchpoints", True)
    claude_max_turns = opts.get("claude_max_turns", 10)
    claude_tools = opts.get("claude_allowed_tools", ["Read", "Edit", "Grep", "Glob"])

    if args.no_claude:
        use_claude = False

    sync_order = manifest.get("sync_order", [])
    forks = manifest.get("forks", {})

    # Process each fork
    for fork_name in sync_order:
        if args.fork_filter and fork_name != args.fork_filter:
            continue

        log("=" * 42)
        log(f"Processing fork: {fork_name}")
        log("=" * 42)

        fork = forks.get(fork_name)
        if not fork:
            warn(f"Fork '{fork_name}' in sync_order but not defined in forks. Skipping.")
            continue

        remote = fork["remote"]
        url = fork["url"]
        branch = fork["branch"]
        conflict_policy = fork.get("conflict_policy", "prefer_upstream")
        ref = f"{remote}/{branch}"

        # Setup remote and fetch
        ensure_remote(remote, url)
        fetch_remote(remote, branch)

        if DRY_RUN:
            log(f"(dry-run) Would process {fork_name} from {ref}")
            log("(dry-run) Skipping actual checkout and merge steps")
            continue

        # -------------------------------------------------------------------
        # Phase 1: Pull exclusive paths (deterministic, no merge needed)
        # -------------------------------------------------------------------
        log("--- Phase 1: Exclusive paths ---")

        exclusive = fork.get("exclusive_paths", [])
        widgets = fork.get("widget_entries", [])
        all_exclusive = [p for p in exclusive + widgets if p]

        if all_exclusive:
            pulled = checkout_paths(ref, all_exclusive)
            if pulled:
                git("add", "--", *pulled)
                log(f"Phase 1 complete: {len(pulled)} path(s) pulled")
            else:
                warn("Phase 1: No paths were successfully pulled")
        else:
            log("Phase 1: No exclusive paths defined")

        # -------------------------------------------------------------------
        # Phase 2: Reconcile core touchpoints (may need Claude Code)
        # -------------------------------------------------------------------
        log("--- Phase 2: Core touchpoints ---")

        touchpoints = [p for p in fork.get("core_touchpoints", []) if p]

        if not touchpoints:
            log("Phase 2: No touchpoints defined, skipping")
        else:
            # Check which touchpoints have differences
            needs_work = []
            for tp in touchpoints:
                diff = git_output("diff", ref, "--", tp)
                if diff is None or diff == "":
                    log(f"  {tp}: already in sync")
                else:
                    log(f"  {tp}: differences detected")
                    needs_work.append(tp)

            if not needs_work:
                log("Phase 2: All touchpoints already in sync")

            else:
                file_list = " ".join(needs_work)
                claude_succeeded = False

                if use_claude and find_claude():
                    # Build prompt with conflict policy
                    if conflict_policy == "prefer_source":
                        policy = (
                            "CONFLICT POLICY: prefer_source — This is the user's OWN branch. "
                            "Their changes are intentional. On conflicts, KEEP THE SOURCE BRANCH "
                            "version. Upstream main is the base to add to, not the authority."
                        )
                    else:
                        policy = (
                            "CONFLICT POLICY: prefer_upstream — This is an EXTERNAL fork. "
                            "Upstream likely has newer fixes. On conflicts, KEEP UPSTREAM's "
                            "version. Only integrate clearly additive content from the fork."
                        )

                    prompt = (
                        f"Reconcile these core touchpoint files between upstream (main) "
                        f"and source '{fork_name}' (remote: {ref}).\n\n"
                        f"{policy}\n\n"
                        f"Files to check: {file_list}"
                    )

                    system_prompt_file = script_dir / "claude_merge_prompt.txt"
                    claude_succeeded = run_claude(prompt, system_prompt_file, claude_tools, claude_max_turns)
                elif use_claude:
                    warn("Claude Code CLI not found.")

                # If Claude didn't run or failed, always show diffs for manual review
                if not claude_succeeded:
                    log("Printing diffs for manual review:")
                    for tp in needs_work:
                        print(f"\n--- {tp} ---")
                        git("diff", ref, "--", tp)
                    warn(f"Manual reconciliation needed for: {file_list}")

        # -------------------------------------------------------------------
        # Phase 3: Commit (if auto_commit enabled)
        # -------------------------------------------------------------------
        if auto_commit:
            check = git("diff", "--cached", "--quiet", check=False, capture=True)
            if check.returncode == 0:
                log(f"No changes to commit for {fork_name}")
            else:
                msg = f"{commit_prefix} Sync {fork_name} ({branch})"
                log(f"Committing: {msg}")
                git("commit", "-m", msg)
        else:
            log("Auto-commit disabled. Changes are staged but not committed.")

        log(f"Done: {fork_name}")
        print()

    log("All forks processed.")


if __name__ == "__main__":
    main()
