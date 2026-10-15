#!/usr/bin/env bash
# Project-owned private tunnel to the resident inference engine, not a skill UI.
set -euo pipefail
exec ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:18889:127.0.0.1:8879 pai-dsw-aliyun-0711
