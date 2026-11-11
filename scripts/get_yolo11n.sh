#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PYTHON_BIN="$(which python3.12 || which python3)"
"$PYTHON_BIN" -m venv "$TMP/venv"
"$TMP/venv/bin/python" -m pip install --upgrade pip
"$TMP/venv/bin/pip" install --extra-index-url https://download.pytorch.org/whl/cpu "ultralytics==8.3.0" "torch>=2.4.0" "torchvision" "onnx>=1.16.0"
export PATH="$TMP/venv/bin:$PATH"
mkdir -p "$ROOT/models"
cd "$TMP"
"$TMP/venv/bin/yolo" export model=yolo11n.pt format=onnx imgsz=640 simplify=False
cp "$TMP/yolo11n.onnx" "$ROOT/models/yolo11n.onnx"
echo "Exported $ROOT/models/yolo11n.onnx (AGPL-3.0)."
