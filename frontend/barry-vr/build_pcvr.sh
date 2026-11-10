#!/usr/bin/env bash
# ==============================================================================
# BARRY PC-VR Standalone Build Helper (Option 3)
# ==============================================================================
# Configures and builds the Barry Unity project for Standalone PC-VR.
# When run, the executable connects to your PC OpenXR runtime (Meta Quest Link,
# Air Link, or SteamVR) without requiring APK installation or Quest Developer Mode.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_URL="${BARRY_BACKEND_URL:-http://192.168.0.156:8000}"
UNITY_BIN="${UNITY_EXECUTABLE:-unity-editor}"

echo "=== Barry PC-VR Standalone Setup ==="
echo "Project Path : ${SCRIPT_DIR}"
echo "Backend URL  : ${BACKEND_URL}"

if ! command -v "${UNITY_BIN}" &>/dev/null; then
    echo "[!] Unity executable ('${UNITY_BIN}') not found in PATH."
    echo "    To build from command line, specify UNITY_EXECUTABLE, e.g.:"
    echo "    UNITY_EXECUTABLE='/path/to/Unity' ./build_pcvr.sh"
    echo ""
    echo "    Or open 'frontend/barry-vr' in the Unity Editor and select:"
    echo "    Barry > Build PC-VR (Windows) from the top menu."
    exit 0
fi

OUTPUT="${BARRY_PCVR_OUTPUT:-${SCRIPT_DIR}/Builds/PCVR/Barry.exe}"
echo "Output       : ${OUTPUT}"
echo "[*] Building Windows PC-VR player via Unity batchmode (close the Editor first)..."
BARRY_BACKEND_URL="${BACKEND_URL}" "${UNITY_BIN}" \
    -batchmode \
    -quit \
    -projectPath "${SCRIPT_DIR}" \
    -executeMethod Barry.Build.Editor.QuestBuildSetup.BuildPCVR \
    -buildOutput "${OUTPUT}" \
    -logFile "${SCRIPT_DIR}/Builds/pcvr-build.log"

echo "[+] PC-VR build complete: zip $(dirname "${OUTPUT}") and send the whole folder."
