#!/usr/bin/env bash
# .claude/hooks/protect-frozen-paths.sh
# PreToolUse for Write|Edit|MultiEdit. Enforces Invariant 1: contracts are frozen.

set -euo pipefail

INPUT="$(cat)"
TOOL="$(printf '%s' "$INPUT" | jq -r '.tool_name // ""')"
FILE="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""')"

# No file path? Nothing to check.
[[ -z "$FILE" ]] && exit 0

# Normalize: if relative, make absolute against cwd
if [[ "$FILE" != /* ]]; then
  FILE="$(realpath -m "$FILE" 2>/dev/null || echo "$FILE")"
fi

# --- Frozen path: rfmesh-contracts source ----------------------------------
if [[ "$FILE" == *"/packages/rfmesh-contracts/src/"* ]] || \
   [[ "$FILE" == *"/packages/rfmesh-contracts/src" ]]; then
  cat >&2 <<EOF
BLOCKED by .claude/hooks/protect-frozen-paths.sh

Target file: $FILE
Tool: $TOOL

Reason: Invariant 1 — packages/rfmesh-contracts/src is FROZEN.

If you believe a contract change is required:
  1. STOP. Do not implement.
  2. Draft an ADR: docs/adr/ADR-NNN-<short>.md (status: PROPOSED).
  3. Surface to the lead. Only the lead unfreezes contracts.

Read AGENTS.md §1 for the ADR format and draft docs/adr/ADR-NNN-<short>.md
with status PROPOSED. Surface to the lead.
EOF
  exit 2
fi

# --- version.py outside rfmesh-contracts ------------------------------------
# SCHEMA_VERSION is owned by rfmesh-contracts. Any version.py anywhere
# else that pretends to bump it is suspicious.
if [[ "$FILE" == *"/version.py" ]] && [[ "$FILE" != *"/rfmesh-contracts/"* ]]; then
  cat >&2 <<EOF
BLOCKED by protect-frozen-paths.sh

Target file: $FILE

Reason: SCHEMA_VERSION is owned by rfmesh-contracts only (AGENTS.md §3).
A version.py outside rfmesh-contracts is forbidden because mypy uses
the Literal[SCHEMA_VERSION] pinning to catch drift across workstreams.
EOF
  exit 2
fi

exit 0
