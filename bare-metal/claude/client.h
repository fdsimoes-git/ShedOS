#pragma once

/* Was the OAuth token baked in at build time (make CLAUDE_TOKEN=...)? */
int claude_has_token(void);

/* Send the current session to the Messages API and write Claude's reply
 * into `reply` (NUL-terminated). Returns 0 on success, -1 on error
 * (in which case `reply` holds an error message). */
int claude_turn(char *reply, int reply_max);
