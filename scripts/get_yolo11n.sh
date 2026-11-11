#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python3.12 -m venv "$TMP/venv"
"$TMP/venv/bin/python" -m pip install --upgrade pip
"$TMP/venv/bin/pip" install "ultralytics==8.3.0" "torch==2.5.1" "torchvision==0.20.1" "onnx==1.17.0" "ml-dtypes<0.5"
export PATH="$TMP/venv/bin:$PATH"
mkdir -p "$ROOT/models"
cd "$TMP"
"$TMP/venv/bin/yolo" export model=yolo11n.pt format=onnx imgsz=640 simplify=False
cp "$TMP/yolo11n.onnx" "$ROOT/models/yolo11n.onnx"
echo "Exported $ROOT/models/yolo11n.onnx (AGPL-3.0)."
