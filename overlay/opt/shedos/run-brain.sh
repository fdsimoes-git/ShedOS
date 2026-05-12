#!/bin/sh
# Bind stdio to tty1 and hand off to brain.py under a new session.
exec </dev/tty1 >/dev/tty1 2>&1
exec setsid /usr/bin/python3 /opt/shedos/brain.py
