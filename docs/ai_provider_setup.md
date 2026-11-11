# BARRY Intelligence Engine — AI Provider Setup & Switching Guide

## Object detection model

The camera detector uses Ultralytics YOLO11n exported to ONNX and runs with CPU `onnxruntime`. YOLO11n is licensed under **AGPL-3.0**; this project has accepted that license for private use. Export it with `scripts/get_yolo11n.sh`. The resulting `models/yolo11n.onnx` is local and ignored by Git. Without a model or live frame source, detection reports unavailable and returns no detections; it never fabricates detections.

BARRY uses a modular **LLM Provider Layer** (`backend/ai/`) so the backend is never locked into a single AI vendor. All providers implement the `LLMProvider` abstract base class (`backend/ai/interface.py`).

---

## 1. Supported Providers

| `LLM_PROVIDER` Value | Implementation Class | Purpose |
| :--- | :--- | :--- |
| `mock` | `MockProvider` | Deterministic offline rule-based intelligence (default when no API key is set) |
| `openrouter` | `OpenRouterProvider` | Unified gateway to OpenAI, Anthropic Claude, Google Gemini, Meta Llama, etc. |
| `local` / `dgx` | `LocalLLMProvider` | OpenAI-compatible local inference on **NVIDIA DGX Spark** (vLLM / Ollama / TensorRT-LLM) |
| `openai` | `OpenAIProvider` | Direct OpenAI API (`https://api.openai.com/v1`) |
| `anthropic` | `AnthropicProvider` | Direct Anthropic Messages API (`https://api.anthropic.com/v1`) |
| `google` | `GoogleProvider` | Direct Google Gemini API (`https://generativelanguage.googleapis.com/v1beta`) |

---

## 2. Environment Configuration (`.env`)

Copy `.env.example` to `.env` and configure your desired provider:

```env
# Primary and fallback provider selection
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-4o-mini
FALLBACK_LLM_PROVIDER=mock

# OpenRouter Gateway (Recommended for cloud multi-model testing)
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# NVIDIA DGX Spark / Local OpenAI-Compatible Inference
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_API_KEY=local-dgx
LOCAL_LLM_MODEL=llama3.1:8b

# Timeouts and retries
AI_TIMEOUT=30
AI_MAX_RETRIES=2
```

> **Zero-Key Startup:** If `OPENROUTER_API_KEY` is empty and `LLM_PROVIDER` is unset, BARRY automatically selects `MockProvider` (`mock`) so local development, VR integration, and automated tests work out of the box.

---

## 3. Switching Models on OpenRouter

Because `LLM_MODEL` is read dynamically from environment configuration, you can test different frontier models through OpenRouter without changing any code:

- `LLM_MODEL=openai/gpt-4o-mini`
- `LLM_MODEL=anthropic/claude-3.5-sonnet`
- `LLM_MODEL=google/gemini-2.0-flash-001`
- `LLM_MODEL=meta-llama/llama-3.3-70b-instruct`

---

## 4. NVIDIA DGX Spark Local Inference Setup

When running local models on **NVIDIA DGX Spark** (via vLLM, TensorRT-LLM, NIM, or Ollama) exposing an OpenAI-compatible `/v1/chat/completions` endpoint:

```env
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://<DGX_SPARK_IP>:8000/v1
LOCAL_LLM_API_KEY=local-dgx
LOCAL_LLM_MODEL=nvidia/Llama-3.1-Nemotron-70B-Instruct
FALLBACK_LLM_PROVIDER=mock
```

Check provider readiness at any time via:
```bash
curl http://localhost:8000/api/ai/status
```

---

## 5. Fallback & Safety Guarantees

1. **Resilience:** `AIOrchestrator` (`backend/ai/orchestrator.py`) wraps all provider calls. If the primary provider times out, hits a rate limit, or returns invalid JSON, BARRY automatically attempts `FALLBACK_LLM_PROVIDER` (default `mock`). If all providers fail, BARRY stores a degraded `unavailable` analysis record and **never** drops the ingested inspection event.
2. **Prompt-Injection Isolation:** All event payloads are wrapped in `<untrusted_observation>` tags and treated strictly as sensor data.
3. **Strict Control Separation:** The LLM is advisory only — it cannot authorize NFC credentials, issue robot movement commands, or override emergency stop.
