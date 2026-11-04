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

## 5. Unity 6 & Meta XR SDK Configuration (Person B)

When Person B is ready to build the standalone VR application:

### Step 1: Project Setup in Unity 6
1. Create a new Unity 6 project with the **3D (URP)** template in [`frontend/unity-project`](file:///home/koiisme/barry/frontend/unity-project).
2. Go to **File** $\rightarrow$ **Build Settings**:
   - Switch Platform to **Android**.
   - Texture Compression: **ASTC**.
3. In **Package Manager**, install:
   - **Meta XR All-in-One SDK** (`com.meta.xr.sdk.all`).
4. In **Project Settings** $\rightarrow$ **XR Plug-in Management**:
   - Check **OpenXR** under the Android tab.
   - Enable the **Meta Quest Feature Group**.

### Step 2: Import BARRY C# Cockpit Scripts
The scripts are already created in the repository under [`frontend/unity-project/Assets/Scripts/`](file:///home/koiisme/barry/frontend/unity-project/Assets/Scripts/):
1. `BarryNetworkManager.cs`: Set `backendHttpUrl` to `http://<LAPTOP_IP>:8000` and `backendWsUrl` to `ws://<LAPTOP_IP>:8000/ws`.
2. `BarryCameraReceiver.cs`: Assign the target `RawImage` to display `/api/camera/stream`.
3. `BarryCockpitUI.cs`: Assign the HUD text, battery meter, and emergency stop button.
4. `BarryInputController.cs`: Maps Touch Controller thumbsticks to locomotion.

### Step 3: Build & Deploy
Click **Build and Run** in Unity. Unity will automatically use ADB to deploy the APK to your Quest 3S.
