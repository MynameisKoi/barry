# Camera API — Setup Log

Record of how the Camera Feed API is configured, and what is still open for
the Raspberry Pi deployment. Plan: `camera_plan.md`. Status as of 2026-11-06.

## 1. What exists

| Piece | File | Notes |
|---|---|---|
| Config (`CAMERA_*` env) | `config.py` | Validated, immutable |
| Interface, errors, `Frame` | `interface.py` | |
| Status model | `status.py` | `null` for unknown measurements |
| Frame sources | `source.py` (contract), `yahboom.py` (OpenCV), `synthetic.py` (no hardware) | |
| Capture service | `service.py` | One capture thread, reconnect with backoff, viewer limit |
| MJPEG generator | `stream.py` | |
| Auth | `auth.py` | `X-Camera-Key` header or `?token=` |
| Router | `router.py` | `/api/v1/camera/{stream,snapshot,status}` |
| Wiring | `factory.py` → `build_camera_service()` | `CAMERA_SOURCE=pi` reads the Pi MJPEG stream; `CAMERA_DEVICE=synthetic` selects test frames |

The router is mounted in both the laptop backend and the Pi Robot API. On the
Pi, the same `CameraService` owns one `YahboomSource` shared by all viewers;
camera startup errors leave robot control available.

## 2. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CAMERA_API_KEY` | none | **Required.** Shared secret. Unset = every camera request returns 503 (fail-closed) |
| `CAMERA_SOURCE` | `auto` | Laptop source selection; `pi` reads `CAMERA_PI_URL` with `ROBOT_API_KEY` |
| `CAMERA_PI_URL` | `http://barry-pi.local:8100/api/v1/camera/stream` | Pi MJPEG stream when source is `pi` |
| `CAMERA_ENABLED` | `true` | `false` skips camera startup |
| `CAMERA_DEVICE` | `0` | Device index, path such as `/dev/video0`, or `synthetic` |
| `CAMERA_WIDTH` / `CAMERA_HEIGHT` | `640` / `480` | |
| `CAMERA_TARGET_FPS` | `20` | Configured rate, not a measured guarantee |
| `CAMERA_JPEG_QUALITY` | `75` | 1–100 |
| `CAMERA_MAX_VIEWERS` | `3` | Extra viewers get HTTP 429 |
| `CAMERA_STALL_TIMEOUT_S` | `2.0` | Seconds without a new frame before the camera counts as stalled |

An invalid `CAMERA_*` value disables the camera (logged as an error) but does
not stop the backend from starting.

Generate a key: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
Never commit the real key.

## 3. Using the API

```
curl -H "X-Camera-Key: $CAMERA_API_KEY" http://<host>:8000/api/v1/camera/status
curl -H "X-Camera-Key: $CAMERA_API_KEY" -o frame.jpg http://<host>:8000/api/v1/camera/snapshot
<img src="http://<host>:8000/api/v1/camera/stream?token=KEY">     (browser / Quest)
```

Status codes: 401 wrong/missing key, 503 key unset or camera unavailable,
429 viewer limit reached. The `?token=` form can end up in logs and browser
history, so use header auth wherever the client can set headers.

Clients: the web cockpit reads `?camera_token=KEY` once and keeps it in local
storage; Unity sends `X-Camera-Key` from `BarryNetworkManager.cameraApiKey`.

## 4. Raspberry Pi setup — TO DO (nothing below has been done or tested on the Pi)

1. **Camera device.** Confirm the real device index or path on the Pi
   (`ls /dev/video*`) and set `CAMERA_DEVICE`. Confirm the Yahboom
   `camera_dogzilla.py` behaviour matches `yahboom.py`; that source file was
   not available in the repo, so `yahboom.py` wraps plain OpenCV.
2. **Only one process may open the camera.** Do not run Yahboom's own
   `app_dogzilla.py` (port 6500) at the same time.
3. **Secrets.** The Pi camera currently authenticates with `ROBOT_API_KEY`
   in the `X-Robot-Key` header. A dedicated emergency key is a future hook.
4. **Network.** Keep the stream on the LAN and reserve a fixed
   address for the Pi. No HTTPS is set up; do not expose this to the internet.
5. **Run under systemd** with a single worker, like the Robot API.
6. **Verify on hardware:** snapshot returns a real JPEG, stream plays in the
   laptop browser and the Quest browser, unplugging the camera shows
   `connected: false` and it recovers when plugged back in, movement commands
   are unaffected while streaming.

## 5. Verified so far

- Automated tests with fake and synthetic sources only (the suite under
  `backend/camera/` plus the full repo suite).
- Not verified: the real DOGZILLA camera, a Pi, Wi-Fi performance, latency, or
  the Quest browser.

## 6. Known open items

- Frame timestamps are taken after read and encode, so a camera that runs
  faster than `CAMERA_TARGET_FPS` can show old buffered frames as fresh.
  Only debug logging exists for now.
- The cockpit and Unity changes have not been run against a live backend.
