# Meta Quest 3S Integration & Setup Guide

This guide walks through the end-to-end setup to connect your **Meta Quest 3S** headset to the **BARRY** backend and deploy the VR Command Center.

---

## 1. Network Architecture & Wi-Fi Pairing

The Meta Quest 3S and the laptop must communicate over the local Wi-Fi router.

```
+---------------------+                      +---------------------+
|   META QUEST 3S     |                      |   PRIMARY LAPTOP    |
| (Meta XR / Browser) |                      |  (FastAPI Backend)  |
| 192.168.1.X:5555    |                      | 192.168.1.Y:8000    |
+---------------------+                      +---------------------+
           \                                            /
            \                                          /
             +---[ Local Wi-Fi Router (Same Subnet) ]-+
```

### VirtualBox Network Configuration
Because the backend runs inside a VirtualBox Linux VM:
- **Recommended Option A (Port Forwarding)**:
  In VirtualBox: Click **Machine** $\rightarrow$ **Settings** $\rightarrow$ **Network** $\rightarrow$ **Advanced** $\rightarrow$ **Port Forwarding**:
  - Add Rule: Host Port `8000` $\rightarrow$ Guest Port `8000`
  - Add Rule: Host Port `5555` $\rightarrow$ Guest Port `5555`
  *The Quest 3S will connect to `http://<HOST_WIFI_IP>:8000`.*
- **Option B (Bridged Adapter)**:
  Change "Attached to: NAT" to "Attached to: Bridged Adapter" (select your physical Wi-Fi card). The VM will get its own local IP address (e.g., `192.168.1.xxx`).

---

## 2. Enabling Developer Mode on Meta Quest 3S

Before you can use ADB or deploy custom Unity builds, enable Developer Mode:
1. Put on the Quest 3S and complete initial setup (Wi-Fi connection).
2. Open the **Meta Quest mobile app** on your phone (iOS / Android).
3. Go to **Menu** $\rightarrow$ **Devices** $\rightarrow$ Select your **Meta Quest 3S**.
4. Go to **Headset Settings** $\rightarrow$ **Developer Mode**.
5. Toggle Developer Mode **ON** (if prompted, register as an organization at [developer.oculus.com](https://developer.oculus.com)).

---

## 3. Connecting via ADB (USB-C & Wireless)

`adb` is pre-installed on this VM.

### Step 1: Connect USB-C
Plug the Quest 3S into the computer via USB-C.
In VirtualBox: Click **Devices** $\rightarrow$ **USB** $\rightarrow$ select **Oculus Quest 3S** (vendor ID `2833`).

### Step 2: Authorize Inside the Headset
Put on the headset. You will see a dialog:
> *"Allow USB debugging?"*
Check **"Always allow from this computer"** and click **Allow**.

### Step 3: Verify Connection
In the terminal:
```bash
adb devices
```
*Expected output: `<device-serial> device` (if it says `unauthorized`, put on the headset and click Allow).*

### Step 4: Enable Wireless ADB (Untethered)
To disconnect the USB cable and work completely wirelessly:
1. Find the Quest 3S Wi-Fi IP in the headset (**Settings** $\rightarrow$ **Wi-Fi** $\rightarrow$ select network $\rightarrow$ look at IP address, e.g., `192.168.1.120`).
2. Run:
   ```bash
   adb tcpip 5555
   adb connect <QUEST_IP>:5555
   ```
3. Disconnect the USB cable. The headset is now connected over Wi-Fi!

---

## 4. Instant Headset Test: Meta Quest Browser

You can test the BARRY cockpit inside your Quest 3S right now **without waiting for a Unity build**:
1. Put on the Quest 3S.
2. Open the built-in **Meta Quest Browser**.
3. In the URL bar, type:
   ```text
   http://<YOUR_LAPTOP_IP>:8000/cockpit
   ```
4. A giant floating cybernetic cockpit window will appear in front of you in your room/passthrough.
5. Use your Touch Controllers to:
   - Watch the live camera stream with AI bounding boxes.
   - Steer the robot forward/left/right.
   - Tap your physical Flipper card to authenticate live!

---

## 5. Unity Project, Quest APK and PC-VR Build

The Unity project is **`frontend/barry-vr/`** (Unity 6, editor version `6000.6.8f1`). Open that folder in Unity Hub. Do not create a new project. The Meta OpenXR, OpenXR and XR Interaction Toolkit packages are already listed in `frontend/barry-vr/Packages/manifest.json`.

Install these Unity modules in Unity Hub → Installs:
- **Android Build Support** for the Quest APK.
- **Windows Build Support (Mono)** for the PC-VR build (see Option 3 below).

### Step 1: Set the backend URL and keys
Set these environment variables before you open Unity, or before a batch build:

| Variable | Value |
|---|---|
| `BARRY_BACKEND_URL` | `http://<LAPTOP_IP>:8000` |
| `BARRY_ROBOT_KEY` | The laptop's `QUEST_API_KEY`. The app sends it as `X-Robot-Key`. |
| `BARRY_CAMERA_KEY` | The laptop's `CAMERA_API_KEY`. |

The **Barry → Configure Quest Build** menu writes these into `frontend/barry-vr/Assets/Barry/Config/BarryConfig.asset`. That asset is tracked in git, so **do not commit it with real keys**. Restore it with `git checkout` before you commit anything else.

### Step 2: Configure the Quest build
In the Unity editor, choose **Barry → Configure Quest Build**. It:
- switches the platform to Android (IL2CPP, ARM64, Vulkan, minimum API 32);
- enables OpenXR with the Meta Quest feature group;
- sets the Dashboard scene (`Assets/Barry/Scenes/Dashboard.unity`) as the only scene in the build;
- writes the backend URL and keys from Step 1 into `BarryConfig.asset`.

Check that **Project Settings → XR Plug-in Management → Android** shows OpenXR.

### Step 3: Build and run on the Quest (APK)
Connect the Quest over ADB (Section 3), then click **File → Build and Run** in Unity. Unity installs the APK on the headset with ADB.

### The scripts are already in the project
You do not import any scripts. The app code is in `frontend/barry-vr/Assets/Barry/`. The files you may need to check:
- `Api/BarryApiClient.cs`: calls the laptop `/api/v1/*` routes and sends `X-Robot-Key`.
- `Api/MjpegStreamSource.cs`: reads the camera stream.
- `Config/BarryConfig.cs`: the backend URL and keys.
- `App/DashboardController.cs`, `App/BarryApp.cs`: the dashboard.
- `XR/ControllerInputReader.cs`, `Teleop/InputMapping.cs`, `Teleop/RobotControlService.cs`: controller input and robot control.

### Option 3: PC-VR over Quest Link (Windows PC required)
Option 3 runs the same app as a Windows PC player. The headset connects to the PC through **Meta Quest Link** or **Air Link**. **Quest Link and Air Link run only on Windows, so you need a Windows PC to run the PC-VR build.** The build can be made from a Mac Unity editor, because it builds a Windows player with the Mono backend.

- In the editor: **Barry → Build PC-VR (Windows)**. The output is `frontend/barry-vr/Builds/PCVR/Barry.exe`.
- Headless (macOS or Linux, Unity closed): `UNITY_EXECUTABLE=<path-to-Unity> frontend/barry-vr/build_pcvr.sh`. Set `BARRY_BACKEND_URL` to override the script's default backend URL. The log is written to `frontend/barry-vr/Builds/pcvr-build.log`.
- Copy the **whole** `Builds/PCVR/` folder to the Windows PC and run `Barry.exe` there. Do not copy the `.exe` alone.
