# BARRY Setup & Development Guide

This guide describes how to run and verify the backend environment on the primary laptop.

---

## 1. Prerequisites
- Python 3.10+ (tested on Python 3.13)
- Git
- Local Wi-Fi network connection (shared between laptop and Meta Quest 3S)

---

## 2. Environment Setup

### Clone Repository & Navigate
```bash
cd <repo>
```

### Create and Activate Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Install Dependencies
```bash
pip install -r requirements.txt
```

---

## 3. Running Backend Server

### Development Mode (with hot-reload)
```bash
source .venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Accessing Interactive Docs
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 4. Running Automated Tests
```bash
source .venv/bin/activate
pytest -v
```

---

## 5. Finding Laptop IP for Meta Quest 3S
Meta Quest 3S connects to the laptop over the local Wi-Fi router.

Find your local IP address:
```bash
hostname -I | awk '{print $1}'
```
Use this IP in Unity scripts:
- REST Base URL: `http://<YOUR_IP>:8000`
- WebSocket URL: `ws://<YOUR_IP>:8000/ws`
