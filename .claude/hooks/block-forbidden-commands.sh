#!/usr/bin/env bash
# .claude/hooks/block-forbidden-commands.sh
# PreToolUse for Bash. Enforces AGENTS.md §3 command allowlist.
# stdin: tool call JSON. Exit 2 + stderr = block; Claude sees the message.

set -euo pipefail

INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""')"

# Non-bash or empty: nothing to check
[[ -z "$CMD" ]] && exit 0

deny() {
  local reason="$1"
  cat >&2 <<EOF
BLOCKED by .claude/hooks/block-forbidden-commands.sh

Reason: $reason
Command: $CMD

See AGENTS.md §3 (allowed/forbidden commands).
EOF
  exit 2
}

# Helper: anchor pattern to word boundaries / command separators
check() {
  local pattern="$1"
  local reason="$2"
  if echo "$CMD" | grep -qE "$pattern"; then
    deny "$reason"
  fi
}

# --- Package manager violations ---------------------------------------------
check '(^|[[:space:];&|])pip[[:space:]]+install' \
  "pip install forbidden — uv-only workspace (CLAUDE.md Hard rules)"

check '(^|[[:space:];&|])pip3[[:space:]]+install' \
  "pip3 install forbidden — uv-only workspace"

check '(^|[[:space:];&|])uv[[:space:]]+add([[:space:]]|$)' \
  "uv add requires lead approval (AGENTS.md §5: write /uvadd-request first)"

check '(^|[[:space:];&|])uv[[:space:]]+remove([[:space:]]|$)' \
  "uv remove requires lead approval (same channel as uv add)"

check '(^|[[:space:];&|])(pipenv|poetry|conda|hatch|rye)[[:space:]]' \
  "Only uv is allowed (CLAUDE.md Hard rules)"

# --- Git push violations ----------------------------------------------------
check 'git[[:space:]]+push.*(--force|--force-with-lease)([[:space:]]|$)' \
  "Force push forbidden (AGENTS.md §3)"

check 'git[[:space:]]+push.*--no-verify' \
  "git push --no-verify forbidden (bypasses pre-commit gate)"

check 'git[[:space:]]+push[[:space:]]+-[a-zA-Z]*f([[:space:]]|$)' \
  "Force push (-f flag) forbidden (AGENTS.md §3)"

check 'git[[:space:]]+commit.*--no-verify' \
  "git commit --no-verify forbidden (bypasses pre-commit gate)"

# NOTE 2026-05-17: The two "pushing to main" guards were removed when
# Maciej dissolved the manual four-conversation workflow and handed the
# project to lead-Opus (see HANDOFF_TO_CLAUDE_CODE_LEAD.md §2). The lead
# now owns commits and pushes to main directly. Force-push and
# --no-verify remain blocked because those are real safety guards, not
# multi-agent coordination scaffolds.

# --- Destructive filesystem ops --------------------------------------------
# Block rm with any recursive flag variant: -r, -R, --recursive, -rf, -fr, etc.
check '(^|[[:space:];&|])rm[[:space:]]+(-[a-zA-Z]*[rR]|--recursive)' \
  "rm with recursive flag forbidden (AGENTS.md §3 — mass deletes)"

check '(^|[[:space:];&|])(shred|wipe)[[:space:]]' \
  "shred/wipe forbidden — too easy to misuse"

# --- git merge while on main -----------------------------------------------
# `git merge` is contextual — it operates on the current branch. We block
# it only when HEAD is main. Anywhere else (merging main *into* a feature
# branch, e.g.) is fine.
if echo "$CMD" | grep -qE '(^|[[:space:];&|])git[[:space:]]+merge'; then
  CURRENT="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
  if [[ "$CURRENT" == "main" ]]; then
    deny "git merge while on main forbidden — only the lead merges (AGENTS.md §3, §7)"
  fi
fi

# --- Belt-and-suspenders: bash-side writes to frozen contracts -------------
# Write/Edit tools are caught by protect-frozen-paths.sh. But a bash
# redirect like `echo 'hax' > packages/rfmesh-contracts/src/foo.py` would
# bypass that. Catch it here.
if echo "$CMD" | grep -qE 'packages/rfmesh-contracts/src' && \
   echo "$CMD" | grep -qE '(>>?[[:space:]]|tee[[:space:]]|sed[[:space:]]+-i|cp[[:space:]]|mv[[:space:]]|truncate[[:space:]])'; then
  deny "Bash modification of rfmesh-contracts/src forbidden (Invariant 1: contracts frozen)"
fi

# Default: allow
exit 0
