#pragma once

#define SESSION_MAX_MSGS 200
#define SESSION_MAX_LEN  16384

typedef struct {
    char role[12];    /* "user" or "assistant" */
    char *content;    /* heap-allocated */
} msg_t;

void   session_init(void);
void   session_add(const char *role, const char *content);
msg_t *session_messages(int *count_out);
void   session_clear(void);
