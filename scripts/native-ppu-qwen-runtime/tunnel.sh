#!/usr/bin/env bash
set -euo pipefail
exec ssh -fN -o BatchMode=yes -o ExitOnForwardFailure=yes \
 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
 -L 127.0.0.1:18891:127.0.0.1:8891 pai-dsw-aliyun-0711
