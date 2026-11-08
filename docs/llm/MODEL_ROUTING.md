# Model Routing & Local Inference Strategy

## Current baseline (source audit)

- Settings defaults to provider=ollama.
- cli._build_llm() creates OllamaClient; native /api/chat is used for inference.
- /api/tags inventories installed models; health_check exercises a real generation.
- Studio creates AgentLoop with the same provider builder.
- kardec-agent doctor checks model installation and response; kardec-agent models lists installed tags plus curated candidates.
- Catalog/doctor prove code path and service response only when executed locally; they do not certify model quality.

## Adaptive routing design

Goal: choose the smallest model that reliably completes the task; prioritize verified quality, then latency/memory. Escalate when planning, tool schema, tests or verification fail. Never silently downgrade after failure.

Profiles (configurable heuristics):
- fast: classify, extract, summarize, small docs; 0.8B–4B.
- balanced: ordinary code changes, tests, routine planning; 4B–9B.
- strong: multi-file refactors, debugging, architecture; 9B–27B+.
- experimental: uncensored/abliterated research models; explicit opt-in only.

Pipeline:
1. Detect hardware (OS/GPU/VRAM/RAM); represent unknown separately from zero.
2. Inventory Ollama models and /api/show metadata (family, parameter size, quantization, context, capabilities). Cache bounded metadata and support refresh.
3. Classify task via cheap deterministic signals first (scope, files, debugging/refactor/tests); optional small classifier only when ambiguous. User can override profile/model.
4. Filter by installed state, tool capability, context budget, memory headroom and license/provenance.
5. Select based on benchmark history per task category—not parameter count alone.
6. Escalate one tier after bounded failures; preserve failure evidence and avoid replaying destructive actions.
7. Emit model.selected / model.escalated / model.completed events (model, profile, reason, latency, tokens when available); UI shows the choice and override.

## Memory reality

Weight size is not peak VRAM. KV cache grows with context and concurrency; runtime buffers, GPU workspace and desktop usage add overhead. MoE active parameters do not equal resident weight memory. Avoid loading multiple models concurrently and preserve headroom.

## Initial catalog candidates

- Very small: Qwen 3.5 0.8B / 2B; Granite 4 3B.
- Compact: Qwen 3.5 4B; Gemma 4 E4B.
- Balanced: Qwen 3.5 9B.
- Strong local: Qwen 3.8 27B (~18 GB weights class, plus overhead).
- Less-restrictive experiments: Dolphin 3 8B, Dolphin Mistral 7B, Llama 2 Uncensored 7B. These are opt-in, older/community variants; validate tools, provenance and license. "Uncensored" is not a quality metric.
- Qwen 3.8 Flash Next is 125B total / 6B active MoE, ~105 GB MLX package; not a lightweight consumer-GPU model. MLX is Apple Silicon-oriented.

## Engines worth evaluating

- Ollama: initial supported backend, simplest management.
- llama.cpp + GGUF: broad CPU/GPU support; Q4_K_M/Q5_K_M and IQ/imatrix quantizations deserve per-model tests.
- Unsloth Dynamic GGUF: selective layer quantization; benchmark each artifact independently.
- ExLlamaV2/EXL2: GPU-focused alternative, especially NVIDIA; not a drop-in Ollama backend.
- vLLM + AWQ/GPTQ/FP8/LLM Compressor: server throughput and multi-request use; probably heavier than a single-user desktop needs.

Keep a provider/backend abstraction; do not add every engine as a desktop dependency.

## Benchmark gate

Measure plan validity, Portuguese instruction adherence, tool-call correctness, edit/test success, recovery, time-to-first-token, total latency, tokens/s and peak VRAM/RAM. Automatic routing only uses models passing agent/tool reliability thresholds. Show a Pareto frontier, not a single universal winner.

## References

- Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
- Catalog: https://ollama.com/library
- llama.cpp quantization: https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md
- Unsloth Dynamic: https://www.unsloth.ai/blog/dynamic-v2
- vLLM quantization: https://docs.vllm.ai/en/latest/features/quantization/
