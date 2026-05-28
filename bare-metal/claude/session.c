#include "session.h"
#include "../mm/heap.h"
#include "../lib/printf.h"
#include <string.h>

static msg_t msgs[SESSION_MAX_MSGS];
static int   count;

void session_init(void) {
    count = 0;
}

void session_add(const char *role, const char *content) {
    if (count >= SESSION_MAX_MSGS) {
        /* Evict oldest message (shift left by 1, free its content) */
        free(msgs[0].content);
        memmove(&msgs[0], &msgs[1], sizeof(msg_t) * (SESSION_MAX_MSGS - 1));
        count--;
    }
    msg_t *m = &msgs[count++];
    strncpy(m->role, role, sizeof(m->role) - 1);
    m->role[sizeof(m->role)-1] = '\0';
    size_t len = strlen(content);
    if (len > SESSION_MAX_LEN) len = SESSION_MAX_LEN;
    m->content = malloc(len + 1);
    if (m->content) {
        memcpy(m->content, content, len);
        m->content[len] = '\0';
    }
}

msg_t *session_messages(int *count_out) {
    if (count_out) *count_out = count;
    return msgs;
}

void session_clear(void) {
    for (int i = 0; i < count; i++)
        free(msgs[i].content);
    count = 0;
}
