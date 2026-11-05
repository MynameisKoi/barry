# BARRY (the dog) — AI-Powered Immersive Robotic Inspection System

**BARRY** is a central AI intelligence and security backend for an immersive robotic inspection system combining:
- **DOGZILLA S1/S2** quadruped robot locomotion, camera streaming, and sensor telemetry
- **Meta Quest 3S** VR command center (HTTP REST + real-time WebSocket `/ws`)
- **Flipper Zero** physical NFC / 125 kHz RFID operator credential authorization
- **BARRY AI Intelligence Engine (v0.1)** with a modular LLM Provider Layer (**OpenRouter** gateway, **MockProvider** offline default, direct **OpenAI / Anthropic / Google Gemini** adapters, and **NVIDIA DGX Spark** local inference)

---

## Architecture Overview

```text
                   META QUEST 3S (Unity / C# / Web Cockpit)
                               |
                        HTTP / WebSocket
                               |
                               v
+---------------------------------------------------------------+
|                         BARRY BACKEND                         |
|                         FastAPI + SQLite                      |
|                                                               |
|  +-------------------+   +---------------------------------+  |
|  | Mission Manager   |   | Authorization Service (Flipper) |  |
|  +-------------------+   +---------------------------------+  |
|  +-------------------+   +---------------------------------+  |
|  | Event Logger      |   | AI Intelligence Orchestrator    |  |
|  +-------------------+   +---------------------------------+  |
|                                           |                   |
|                                           v                   |
|                                  LLM Provider Layer           |
|              (Mock | OpenRouter | DGX Spark Local | Direct)   |
+---------------------------------------------------------------+
```

---

## Quick Start

```bash
cd /home/koiisme/barry
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Start backend server (runs with MockProvider by default if no API key is set)
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Interactive Web Cockpit:** `http://localhost:8000/cockpit`
- **OpenAPI / Swagger UI:** `http://localhost:8000/docs`
- **AI Provider Status:** `http://localhost:8000/api/ai/status`

---

## Running Automated Tests

```bash
.venv/bin/pytest tests/ -v
```

---

## Documentation

- [API Contracts & WebSocket Specification](docs/api_contracts.md)
- [AI Provider Setup & NVIDIA DGX Spark Guide](docs/ai_provider_setup.md)
- [Development & Testing Guide](docs/development_guide.md)
- [Meta Quest 3S VR Setup Guide](docs/quest3s_setup.md)
