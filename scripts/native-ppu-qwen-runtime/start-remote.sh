#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /mnt/data/workdir/yanlun/housignal/native_qwen_runtime
exec 9>launch.lock
flock -n 9 || { echo 'An owned launch is already active'; exit 1; }
python preflight.py
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="${LIBRARY_PATH:-}"
source /usr/local/PPU_SDK/envsetup.sh cuda >/dev/null
export CUDA_VISIBLE_DEVICES=0
export UMD_PLATFORM_TYPE=1 HGGC_DRIVER_CANDIDATE=UMD
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
exec nice -n 5 python -u native_runtime.py \
  --model-path /mnt/data/workdir/yanlun/experiments/minimax_h3_gwm_compare_20260922/models/text_encoder \
  --auth-file /mnt/data/workdir/yanlun/housignal/native_qwen_runtime/native-qwen.auth \
  --manifest /mnt/data/workdir/yanlun/housignal/native_qwen_runtime/manifest.json \
  --port 8891 --timeout-seconds 280
