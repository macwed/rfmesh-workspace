// Linenoise emergency shell for human debugging via plain `screen`/`minicom`.
// Spec §6: triple-ENTER on a fresh connect to enter, `proto` to leave.

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// Run the linenoise REPL until the user types `proto\n`. Blocks the
// caller's task. Safe to call multiple times across mode transitions.
void shell_run(void);

#ifdef __cplusplus
}
#endif
