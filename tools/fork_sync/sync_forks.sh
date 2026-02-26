#!/usr/bin/env bash
#
# sync_forks.sh — Pull fork-exclusive content and reconcile shared touchpoints.
#
# Usage:
#   ./sync_forks.sh [--dry-run] [--no-claude] [--fork <name>] [--manifest <path>] [--repo <path>]
#
# Requires: git, jq, claude (Claude Code CLI — only for touchpoint reconciliation)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${SCRIPT_DIR}/fork_manifest.json"
REPO_ROOT=""

DRY_RUN=false
NO_CLAUDE=false
FORK_FILTER=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=true; shift ;;
    --no-claude) NO_CLAUDE=true; shift ;;
    --fork)      FORK_FILTER="$2"; shift 2 ;;
    --manifest)  MANIFEST="$2"; shift 2 ;;
    --repo)      REPO_ROOT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,/^$/s/^# //p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers (defined early — needed by REPO_ROOT resolution)
# ---------------------------------------------------------------------------
log()  { echo "[sync] $*"; }
warn() { echo "[sync] WARNING: $*" >&2; }
die()  { echo "[sync] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Resolve REPO_ROOT
# ---------------------------------------------------------------------------
if [[ -z "$REPO_ROOT" ]]; then
  # Try to infer from script location (works when script lives inside the repo)
  REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [[ -z "$REPO_ROOT" ]]; then
  # Fall back to current directory
  REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
[[ -n "$REPO_ROOT" ]] || die "Could not find git repo. Use --repo <path> or run from inside the repo."

jq_read() { jq -r "$1" "$MANIFEST"; }

ensure_remote() {
  local name="$1" url="$2"
  if ! git remote get-url "$name" &>/dev/null; then
    log "Adding remote: $name -> $url"
    $DRY_RUN && return 0
    git remote add "$name" "$url"
  fi
}

fetch_remote() {
  local remote="$1" branch="$2"
  log "Fetching $remote/$branch ..."
  $DRY_RUN && return 0

  local attempt=0
  while (( attempt < 4 )); do
    if git fetch "$remote" "$branch" 2>/dev/null; then
      return 0
    fi
    attempt=$((attempt + 1))
    local wait=$((2 ** attempt))
    warn "Fetch failed (attempt $attempt/4), retrying in ${wait}s ..."
    sleep "$wait"
  done
  die "Failed to fetch $remote/$branch after 4 attempts"
}

checkout_paths() {
  local ref="$1"
  shift
  for path in "$@"; do
    if git ls-tree --name-only -r "$ref" -- "$path" &>/dev/null; then
      log "  Pulling: $path"
      $DRY_RUN && continue
      # Ensure parent directory exists (for new paths not yet in our tree)
      local dir
      # If path ends with /, it's a directory — parent is itself
      # Otherwise, parent is dirname
      if [[ "$path" == */ ]]; then
        dir="$REPO_ROOT/$path"
      else
        dir="$(dirname "$REPO_ROOT/$path")"
      fi
      mkdir -p "$dir"
      git checkout "$ref" -- "$path"
    else
      warn "  Path not found in $ref: $path (skipping)"
    fi
  done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
cd "$REPO_ROOT"

# Validate manifest
[[ -f "$MANIFEST" ]] || die "Manifest not found: $MANIFEST"
command -v jq &>/dev/null || die "jq is required but not installed"

# Read sync options
AUTO_COMMIT=$(jq_read '.sync_options.auto_commit // true')
COMMIT_PREFIX=$(jq_read '.sync_options.commit_prefix // "[fork-sync]"')
USE_CLAUDE=$(jq_read '.sync_options.use_claude_for_touchpoints // true')
CLAUDE_MAX_TURNS=$(jq_read '.sync_options.claude_max_turns // 10')
CLAUDE_TOOLS=$(jq_read '.sync_options.claude_allowed_tools // "Read,Edit,Grep,Glob"')

$NO_CLAUDE && USE_CLAUDE=false

# Read fork order
SYNC_ORDER=()
while IFS= read -r p; do
  [[ -n "$p" ]] && SYNC_ORDER+=("$p")
done < <(jq_read '.sync_order[]')

# Process each fork
for fork_name in "${SYNC_ORDER[@]}"; do
  # Optional filter
  if [[ -n "$FORK_FILTER" && "$fork_name" != "$FORK_FILTER" ]]; then
    continue
  fi

  log "=========================================="
  log "Processing fork: $fork_name"
  log "=========================================="

  remote=$(jq_read ".forks[\"$fork_name\"].remote")
  url=$(jq_read ".forks[\"$fork_name\"].url")
  branch=$(jq_read ".forks[\"$fork_name\"].branch")
  conflict_policy=$(jq_read ".forks[\"$fork_name\"].conflict_policy // \"prefer_upstream\"")
  ref="$remote/$branch"

  # Setup remote and fetch
  ensure_remote "$remote" "$url"
  fetch_remote "$remote" "$branch"

  if $DRY_RUN; then
    log "(dry-run) Would process $fork_name from $ref"
    log "(dry-run) Skipping actual checkout and merge steps"
    continue
  fi

  # -----------------------------------------------------------------------
  # Phase 1: Pull exclusive paths (deterministic, no merge needed)
  # -----------------------------------------------------------------------
  log "--- Phase 1: Exclusive paths ---"

  all_exclusive=()
  while IFS= read -r p; do
    [[ -n "$p" ]] && all_exclusive+=("$p")
  done < <(jq_read ".forks[\"$fork_name\"].exclusive_paths[]? // empty")
  while IFS= read -r p; do
    [[ -n "$p" ]] && all_exclusive+=("$p")
  done < <(jq_read ".forks[\"$fork_name\"].widget_entries[]? // empty")

  if [[ ${#all_exclusive[@]} -gt 0 ]]; then
    checkout_paths "$ref" "${all_exclusive[@]}"
    git add -- "${all_exclusive[@]}"
    log "Phase 1 complete: ${#all_exclusive[@]} path(s) pulled"
  else
    log "Phase 1: No exclusive paths defined"
  fi

  # -----------------------------------------------------------------------
  # Phase 2: Reconcile core touchpoints (may need Claude Code)
  # -----------------------------------------------------------------------
  log "--- Phase 2: Core touchpoints ---"

  touchpoints=()
  while IFS= read -r p; do
    [[ -n "$p" ]] && touchpoints+=("$p")
  done < <(jq_read ".forks[\"$fork_name\"].core_touchpoints[]? // empty")

  if [[ ${#touchpoints[@]} -eq 0 ]]; then
    log "Phase 2: No touchpoints defined, skipping"

  elif [[ "$USE_CLAUDE" == "true" ]]; then
    # Check if Claude Code CLI is available
    if ! command -v claude &>/dev/null; then
      warn "Claude Code CLI not found. Falling back to diff-only mode."
      USE_CLAUDE=false
    fi
  fi

  if [[ ${#touchpoints[@]} -gt 0 ]]; then
    # Build a diff summary for each touchpoint
    touchpoint_args=""
    needs_reconciliation=false

    for tp in "${touchpoints[@]}"; do
      diff_output=$(git diff "$ref" -- "$tp" 2>/dev/null || true)
      if [[ -z "$diff_output" ]]; then
        log "  $tp: already in sync with fork"
      else
        log "  $tp: differences detected"
        needs_reconciliation=true
        touchpoint_args="$touchpoint_args $tp"
      fi
    done

    if $needs_reconciliation; then
      if [[ "$USE_CLAUDE" == "true" ]]; then
        log "Invoking Claude Code for touchpoint reconciliation ..."

        # Build the prompt with file list and conflict policy
        if [[ "$conflict_policy" == "prefer_source" ]]; then
          policy_text="CONFLICT POLICY: prefer_source — This is the user's OWN branch. Their changes are intentional. On conflicts, KEEP THE SOURCE BRANCH version. Upstream main is the base to add to, not the authority."
        else
          policy_text="CONFLICT POLICY: prefer_upstream — This is an EXTERNAL fork. Upstream likely has newer fixes. On conflicts, KEEP UPSTREAM's version. Only integrate clearly additive content from the fork."
        fi

        tp_prompt="Reconcile these core touchpoint files between upstream (main) and source '$fork_name' (remote: $ref).

$policy_text

Files to check: $touchpoint_args"

        claude -p "$tp_prompt" \
          --append-system-prompt-file "${SCRIPT_DIR}/claude_merge_prompt.txt" \
          --allowedTools "$CLAUDE_TOOLS" \
          --max-turns "$CLAUDE_MAX_TURNS" \
          --output-format text

        log "Claude Code reconciliation complete"
      else
        log "Claude disabled. Printing diffs for manual review:"
        for tp in "${touchpoints[@]}"; do
          echo "--- $tp ---"
          git diff "$ref" -- "$tp" || true
          echo ""
        done
        warn "Manual reconciliation needed for: $touchpoint_args"
      fi
    else
      log "Phase 2: All touchpoints already in sync"
    fi
  fi

  # -----------------------------------------------------------------------
  # Phase 3: Commit (if auto_commit enabled)
  # -----------------------------------------------------------------------
  if [[ "$AUTO_COMMIT" == "true" ]]; then
    # Check if there are staged changes
    if git diff --cached --quiet; then
      log "No changes to commit for $fork_name"
    else
      commit_msg="$COMMIT_PREFIX Sync $fork_name ($branch)"
      log "Committing: $commit_msg"
      git commit -m "$commit_msg"
    fi
  else
    log "Auto-commit disabled. Changes are staged but not committed."
  fi

  log "Done: $fork_name"
  echo ""
done

log "All forks processed."
