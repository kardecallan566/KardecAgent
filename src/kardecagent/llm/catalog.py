from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    family: str
    parameters: str
    quantization: str
    vram_gb: str
    role: str
    notes: str
    license: str = "Open weights; check model card"


# Memory estimates are indicative model-weight footprints, not guaranteed peak VRAM.
# Context/KV cache, runtime buffers, GPU offload and OS overhead add to these values.
LOCAL_MODEL_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("qwen3.5:0.8b", "Qwen 3.5", "0.8B", "Ollama quantized", "~1.3 GB", "ultra-light / classification", "Fast routing, extraction and short drafts."),
    ModelSpec("qwen3.5:2b", "Qwen 3.5", "2B", "Ollama quantized", "~2.7–3.1 GB", "simple tasks", "Good low-memory first pass; verify tool-call reliability."),
    ModelSpec("granite4:3b", "Granite 4", "3B", "Ollama quantized", "~2.1 GB download", "instruction / tools", "Compact model family with tool-use support."),
    ModelSpec("qwen3.5:4b", "Qwen 3.5", "4B", "Ollama quantized", "~3.3–4.0 GB", "light coding / routine tasks", "Balanced low-VRAM candidate; benchmark on repository tasks."),
    ModelSpec("gemma4:4b", "Gemma 4", "E4B", "Ollama quantized", "~3.2–4.5 GB", "general / multimodal", "Strong compact general model; validate agent tool format."),
    ModelSpec("qwen3.5:9b", "Qwen 3.5", "9B", "Ollama quantized", "~6.6–7.6 GB", "default coding / planning", "Candidate for quality-first local default on 8–12 GB VRAM."),
    ModelSpec("dolphin3:8b", "Dolphin 3.0", "8B", "Ollama quantized", "~4.9 GB", "less-restrictive general / coding", "Community fine-tune; benchmark tool calling and quality, not just refusal behavior.", "Llama-derived; verify base-model license"),\n    ModelSpec("dolphin-mistral:7b", "Dolphin Mistral", "7B", "Ollama quantized", "~4.1 GB", "less-restrictive coding", "Older fine-tune; compare against current Qwen/Gemma before using as primary.", "Mistral-derived; verify model card"),\n    ModelSpec("llama2-uncensored:7b", "Llama 2 Uncensored", "7B", "Ollama quantized", "~3.8 GB", "legacy less-restrictive", "Older 4K-context generation; included for experimentation, not recommended as default.", "Llama 2 license"),\n    ModelSpec("qwen3.8:27b", "Qwen 3.8", "27B", "Ollama quantized", "~18 GB", "complex coding / architecture", "High-quality local tier; needs substantial GPU memory."),
    ModelSpec("qwen3.8-flash-next:125b-mlx", "Qwen 3.8 Flash Next", "125B total / 6B active", "MLX", "~105 GB", "large-memory Apple systems", "NOT a lightweight GPU model; MoE active parameters do not remove weight-memory needs."),
)


def find_catalog_model(name: str) -> ModelSpec | None:
    normalized = name.strip().lower()
    return next((item for item in LOCAL_MODEL_CATALOG if item.name.lower() == normalized), None)
