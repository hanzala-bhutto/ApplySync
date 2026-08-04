# Local Model Experiment (8GB VRAM)

One-off experiment, not shipped to `main`. Home for this class of write-up is
`docs/experiments/`, exactly analogous to `docs/feasibility/`.

The pipeline runs on a hosted model (`nvidia/nemotron-3-nano-30b-a3b`) by
default. As an experiment, the LLM client was pointed at a **local
[Ollama](https://ollama.com/) instance** (its OpenAI-compatible endpoint) to see
whether extraction could run fully offline, with no API key and no rate limit,
on consumer hardware: an **8GB VRAM AMD Radeon** card. Reproducing it takes a
small change kept on an experimental branch (not shipped to `main`): point the
client at the local endpoint via `NVIDIA_BASE_URL`, and for local runs drop the
NVIDIA-specific 40 RPM rate limiter and disable the model's reasoning step,
since a local model has neither the per-account cap nor a use for
chain-of-thought here.

Each model was run through the **real `classify_and_extract` node** (identical
prompt and strict `json_schema` structured output) on a representative
confirmation email. Latency is the warm per-call time (model already loaded);
"correct" means it produced the expected `company_name` / `job_title` / `status`
on that sample - an indicative smoke test, not the full gold-eval accuracy.

| Model | Params | ~VRAM (Q4) | Warm latency / call | Correct? |
| --- | --- | --- | --- | --- |
| `nvidia/nemotron-3-nano-30b-a3b` (hosted, reference) | 30B MoE (3B active) | n/a (hosted) | **~0.8s** | yes |
| `mistral:7b` | 7B | 4.4 GB | **18.8s** | yes |
| `qwen2.5:3b` | 3B | ~2 GB | 22.8s | yes |
| `gemma3:1b` | 1B | 0.8 GB | 24.0s | **no** (fails the schema) |
| `gemma2:2b` | 2B | 1.6 GB | 26.6s | yes |
| `qwen2.5:7b-instruct` | 7B | 4.5 GB | 35s | yes |
| `qwen3:4b` (reasoning) | 4B | 3.2 GB | never returns | **no** (spends its whole budget "thinking") |

Findings:

- **Local works, but it is 20-40x slower than the hosted model**, not faster.
  The bottleneck is the strict `json_schema` grammar-constrained decode on
  consumer-GPU inference, not VRAM - so its real value is offline / keyless /
  no-rate-limit testing, not speed.
- **Smaller is not reliably faster or better.** The fastest correct model was a
  7B (`mistral:7b`), the smallest model (`gemma3:1b`) failed the schema
  entirely, and a 2B was slower than a 7B. Latency is dominated by the grammar
  decode and prompt processing, so parameter count barely tracks with it.
- **Reasoning models are unusable here.** `qwen3:4b` spends its entire token
  budget on hidden reasoning and never emits the JSON; Ollama's OpenAI endpoint
  does not reliably honor a "disable thinking" flag. Use a plain instruct model.
- **The hosted "nano" cannot be run on an 8GB card.** Despite the name it is a
  30B Mixture-of-Experts model; all 30B parameters must be resident (~18-20GB at
  4-bit), and its ~0.8s speed comes from datacenter GPUs, not from being small.

Bottom line: NVIDIA's hosted model stays the default. Local is a viable
offline/no-key fallback for testing at ~19s/call (`mistral:7b`) if that
tradeoff is worth it.
