#!/usr/bin/env bash
# .claude/hooks/require-verify.sh
# Stop hook. Enforces AGENTS.md §5: `just verify` must run cleanly
# before declaring a ticket done.
#
# Uses a per-session state file to avoid infinite loops. After 2 blocks
# in the same session, lets through (failsafe — better to under-enforce
# than to lock Claude in a loop).

set -euo pipefail

INPUT="$(cat)"
SESSION_ID="$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"')"
TRANSCRIPT="$(printf '%s' "$INPUT" | jq -r '.transcript_path // ""')"

STATE_DIR="${TMPDIR:-/tmp}"
STATE_FILE="${STATE_DIR}/claude-stop-hook-${SESSION_ID}.state"

# --- Loop prevention --------------------------------------------------------
ATTEMPTS=0
if [[ -f "$STATE_FILE" ]]; then
  ATTEMPTS="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
fi

if (( ATTEMPTS >= 2 )); then
  # We've already blocked twice. Let through to avoid agent thrashing.
  # The reviewer will catch the missing verify in the diff.
  exit 0
fi

# --- No transcript: fail open (can't make a judgment) ----------------------
if [[ -z "$TRANSCRIPT" ]] || [[ ! -f "$TRANSCRIPT" ]]; then
  exit 0
fi

# Read last ~800 lines of transcript. Verify output is typically short
# enough to fit; this is a tradeoff between completeness and speed.
RECENT="$(tail -n 800 "$TRANSCRIPT" 2>/dev/null || echo '')"

block() {
  local msg="$1"
  echo $((ATTEMPTS + 1)) > "$STATE_FILE"
  echo "BLOCKED by .claude/hooks/require-verify.sh" >&2
  echo "" >&2
  echo "$msg" >&2
  exit 2
}

# --- Check 1: was verify run at all? ---------------------------------------
if ! echo "$RECENT" | grep -qE 'just[[:space:]]+(verify|ticket-verify)' ; then
  block "$(cat <<'EOF'
You have not run `just verify` (or `just ticket-verify <pkg>`) in this
session. AGENTS.md §5 requires the verification protocol to pass before
declaring a ticket done.

Run now and paste the output:

  just verify

If you're stopping mid-investigation (not declaring done), say so
explicitly in your last message and try stopping again — the hook
will let you through on the second attempt.
EOF
)"
fi

# --- Check 2: did the verify output look clean? -----------------------------
# Heuristic — look for common failure markers near the verify invocation.
# This is best-effort; the lead's review is the actual quality gate.
if echo "$RECENT" | grep -qE '(=+ FAILED |=+ ERROR |^FAILED |error\[[A-Za-z]|^E[[:space:]]+|^ERROR )' ; then
  block "$(cat <<'EOF'
Recent `just verify` output appears to contain failures (FAILED / ERROR
markers detected). AGENTS.md §5 requires a clean pass.

Fix the failures and re-run:

  just verify

Then paste the clean output.

(If this is a false positive — e.g., the word "FAILED" appears in a
test name or string — try stopping again; the hook will let you through
on the second attempt.)
EOF
)"
fi

# All checks passed. Clean up state file for next session.
rm -f "$STATE_FILE"
exit 0
