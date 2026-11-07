#!/usr/bin/env bash
# Fine-tune and serve the decision model on Apple Silicon with MLX-LM (MIT).
#
# Base model: Qwen/Qwen2.5-0.5B-Instruct (Apache-2.0, about 0.49B parameters; license checked on
# its model card, 2026-11-06). LoRA keeps the base weights frozen and trains small adapters, so
# training fits comfortably in a laptop's unified memory.
#
# On an NVIDIA GPU, the same JSONL trains with TRL or Unsloth and serves on vLLM; the resolver
# only needs an OpenAI-compatible /v1/chat/completions with logprobs.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE=${BRIGHTWATER_DM_BASE:-Qwen/Qwen2.5-0.5B-Instruct}
ADAPTERS=${BRIGHTWATER_DM_ADAPTERS:-decision_model/artifacts/adapters}
PORT=${BRIGHTWATER_DM_PORT:-8081}

uv pip install "mlx-lm==0.32.2"

case "${1:-train}" in
  train)
    mlx_lm.lora --model "$BASE" --train --data decision_model/data/mlx --adapter-path "$ADAPTERS" \
      --fine-tune-type lora --num-layers 16 --batch-size 8 --iters 600 --learning-rate 1e-4 \
      --mask-prompt --val-batches 25 --save-every 200
    python - <<PY
import hashlib, json, pathlib, time
art = pathlib.Path("decision_model/artifacts")
out = pathlib.Path("$ADAPTERS")
w = sorted(out.glob("adapters.safetensors"))
digest = hashlib.sha256(w[0].read_bytes()).hexdigest()[:12] if w else "untrained"
meta = json.dumps({
    "base": "$BASE", "adapters": "adapters/adapters.safetensors", "served_name": "default_model",
    "version": f"brightwater-dm@{digest}", "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=2)
(art / "model.json").write_text(meta)
print("model version", f"brightwater-dm@{digest}")
PY
    ;;
  serve)
    # --model is the base; the adapters are applied at load. mlx-lm 0.32.2 maps only the
    # request model name "default_model" to them (any other name is fetched from Hugging Face),
    # so the resolver asks for "default_model" (served_name in model.json).
    mlx_lm.server --model "$BASE" --adapter-path "$ADAPTERS" --port "$PORT"
    ;;
  *) echo "usage: $0 [train|serve]"; exit 2 ;;
esac
