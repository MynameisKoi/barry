# Barry — Camera Feed API Plan

## 1. Objective

Design and implement a Camera Feed API that streams live video from the DOGZILLA S2's camera, connected to the Raspberry Pi 5, to Barry's laptop backend and Meta Quest 3S.

The Camera API must support:

- Live camera streaming
- Capturing individual snapshots
- Camera connection and streaming status
- Configurable video resolution and frame rate
- Camera initialization and resource cleanup
- Safe remote access
- Multiple viewers without opening the physical camera multiple times
- Reliable operation during network interruptions

**Scope:** Camera capture and video delivery only.

Robot movement, posture, actions, telemetry, mission authorization, and Barry's three operating modes are outside this plan.

---

## 2. System Architecture

```text
DOGZILLA S2 Camera
        |
        | Camera connection
        v
Raspberry Pi 5
        |
        v
Camera Capture Service
(backend/camera/)
        |
        | Capture and JPEG encoding
        v
Camera API (FastAPI)
        |
        | HTTP / MJPEG
        v
Laptop — Barry Backend
        |
        | Video delivery
        v
Meta Quest 3S
        |
        v
Live VR Camera Display
```

### Architecture Requirements

- The Raspberry Pi owns the physical camera connection.
- The camera service captures and encodes frames.
- The camera API exposes frames to authorized clients.
- The laptop acts as a video proxy or relay when needed.
- The Meta Quest 3S displays the video stream through its browser.
- Camera processing must not block the robot's movement-control service.

For the initial implementation, use **MJPEG over HTTP**.

WebRTC can be evaluated later if lower latency or reduced bandwidth becomes necessary.

---

## 3. Yahboom Camera SDK Reference

Use the supplied source files:

### `camera_dogzilla.py`

Contains the `Dogzilla_Camera` class.

The supplied implementation uses OpenCV for camera capture and provides JPEG frame generation.

Its example capture resolution is 640 × 480.

### `app_dogzilla.py`

Contains Yahboom's existing camera streaming implementation.

Relevant behavior:

- Exposes `/video_feed`.
- Streams JPEG frames using multipart MJPEG.
- Uses a Flask application.
- Serves the example application on port `6500`.
- Combines camera streaming with other robot-control responsibilities.

### Integration Requirements

- Reuse the relevant capture and encoding behavior.
- Do not copy Yahboom's entire Flask application into Barry.
- Implement Barry's camera API using FastAPI.
- Keep camera control separate from the robot hardware adapter.
- Avoid running multiple processes that compete for the same camera device.
- Verify that the supplied camera implementation works with the actual DOGZILLA S2 camera.

---

## 4. Camera Streaming API

### 4.1 Live Camera Stream

**Endpoint:** `GET /api/v1/camera/stream`

**Purpose:** Deliver a continuous video feed to the laptop or Meta Quest browser.

Request:

```http
GET /api/v1/camera/stream
```

Response:

```http
HTTP/1.1 200 OK
Content-Type: multipart/x-mixed-replace; boundary=frame
Cache-Control: no-store
```

Each multipart frame contains a JPEG image.

Conceptual frame structure:

```http
--frame
Content-Type: image/jpeg
Content-Length: <jpeg-byte-length>

<jpeg-image-bytes>
```

The stream continues until the client disconnects or the server ends the stream.

### Streaming Requirements

- Use FastAPI's `StreamingResponse`.
- Use a single shared camera capture service.
- Encode frames as JPEG.
- Support continuous frame delivery.
- Handle disconnected clients.
- Avoid accumulating stale frames.
- Limit the number of simultaneous viewers if necessary.
- Release stream-related resources when clients disconnect.
- Return an appropriate error if the camera is unavailable.

### Initial Streaming Configuration

| Setting        | Initial target |
| -------------- | -------------- |
| Protocol       | HTTP           |
| Format         | MJPEG          |
| Image encoding | JPEG           |
| Resolution     | 640 × 480      |
| Frame rate     | 15–20 FPS      |
| JPEG quality   | 75             |
| Transport      | Local network  |

These are proposed starting settings, not verified performance guarantees.

Actual frame rate and latency depend on camera hardware, Raspberry Pi processing, Wi-Fi, and the number of viewers.

---

## 5. Camera Snapshot API

**Endpoint:** `GET /api/v1/camera/snapshot`

**Purpose:** Capture or retrieve one camera frame.

Request:

```http
GET /api/v1/camera/snapshot
```

Successful response:

```http
HTTP/1.1 200 OK
Content-Type: image/jpeg
Cache-Control: no-store
```

Response body: JPEG image bytes.

### Requirements

- Return the latest valid frame.
- Avoid reopening the camera for every request.
- Return an error when no valid frame is available.
- Do not return an outdated frame as though it were live.
- Keep snapshot requests independent of active MJPEG viewers.

The snapshot endpoint can later support image analysis without changing the camera capture architecture.

Image analysis itself is outside this API plan.

---

## 6. Camera Status API

**Endpoint:** `GET /api/v1/camera/status`

**Purpose:** Report the camera service's operational state.

Example response:

```json
{
  "connected": true,
  "capturing": true,
  "streaming": true,
  "resolution": {
    "width": 640,
    "height": 480
  },
  "target_fps": 20,
  "actual_fps": 18.5,
  "active_viewers": 1,
  "last_frame_age_ms": 45
}
```

All values above are illustrative.

### Status Fields

| Field               | Description                                |
| ------------------- | ------------------------------------------ |
| `connected`         | Whether the camera device is accessible    |
| `capturing`         | Whether frame capture is active            |
| `streaming`         | Whether the streaming service is operating |
| `resolution`        | Current frame dimensions                   |
| `target_fps`        | Configured capture frame rate              |
| `actual_fps`        | Measured frame rate, if available          |
| `active_viewers`    | Current stream subscribers                 |
| `last_frame_age_ms` | Time since the last successful capture     |

### Requirements

- Distinguish configured settings from measured performance.
- Return useful status even when the camera is disconnected.
- Do not fabricate FPS or connection measurements.
- Use `null` for unavailable measurements when appropriate.

---

## 7. Camera Lifecycle

The camera service must manage initialization, capture, and cleanup.

### Startup

1. Load camera configuration.
2. Identify the configured camera device.
3. Initialize the camera capture object.
4. Validate that frames can be captured.
5. Start the capture loop.
6. Update camera status.

### Runtime

1. Capture a frame.
2. Validate the frame.
3. Encode it as JPEG.
4. Publish the latest frame to subscribers.
5. Update frame timestamps and performance metrics.
6. Continue until shutdown or a recoverable error occurs.

### Shutdown

1. Stop the capture loop.
2. Close active streaming sessions.
3. Release the camera device.
4. Clear temporary resources.
5. Update the camera status.

### Error Handling

Support:

- Camera not found
- Camera initialization failure
- Frame capture failure
- JPEG encoding failure
- Camera disconnection
- Network client disconnection
- Camera service shutdown

Use bounded retries and avoid infinite busy loops when the camera is unavailable.

---

## 8. Camera Configuration

Centralize camera settings.

Suggested configuration:

```env
CAMERA_ENABLED=true
CAMERA_DEVICE=0
CAMERA_WIDTH=640
CAMERA_HEIGHT=480
CAMERA_TARGET_FPS=20
CAMERA_JPEG_QUALITY=75
CAMERA_MAX_VIEWERS=3
```

These are proposed configuration names.

### Configuration Requirements

- Validate configuration values.
- Allow camera settings to differ between development and Raspberry Pi deployments.
- Avoid hardcoding the camera device.
- Keep network credentials outside source code.
- Support a development camera source or test frames when hardware is unavailable.

The exact camera device index or path must be confirmed on the Raspberry Pi.

---

## 9. Python Module Responsibilities

The existing `backend/camera/` directory should be inspected before choosing class names or creating new files.

Recommended responsibilities:

| Component              | Responsibility                                     |
| ---------------------- | -------------------------------------------------- |
| Camera interface       | Defines capture, snapshot, and status operations   |
| Yahboom camera adapter | Wraps the supplied camera capture implementation   |
| Camera service         | Owns the capture loop and latest frame             |
| Stream generator       | Delivers MJPEG frames to subscribers               |
| Camera API router      | Exposes FastAPI endpoints                          |
| Camera configuration   | Defines device, resolution, FPS, and JPEG settings |
| Camera status model    | Defines structured operational information         |

### Design Principles

- Use object-oriented classes with clear responsibilities.
- Use composition to wrap Yahboom's camera implementation.
- Keep HTTP routes thin.
- Avoid global camera objects scattered across modules.
- Use dependency injection where practical.
- Keep camera capture independent of robot movement.
- Do not modify `backend/robot/` to implement camera features.

---

## 10. Laptop and Meta Quest Integration

The Raspberry Pi is the camera source.

The laptop backend may relay the camera stream to the Meta Quest browser.

### Proposed Communication

```text
Raspberry Pi Camera API
        |
        | MJPEG
        v
Laptop Camera Relay
        |
        | MJPEG
        v
Meta Quest Browser
```

### Requirements

- Verify that the stream can be opened from the laptop.
- Verify browser compatibility.
- Support authenticated access.
- Avoid unnecessary frame re-encoding in the laptop relay.
- Prevent buffering from introducing excessive delay.
- Handle upstream camera failure gracefully.
- Keep video delivery separate from joystick command delivery.

The laptop relay is optional if direct, secure access from the Quest browser to the Pi is practical.

The final routing choice depends on the actual network topology and browser authentication requirements.

---

## 11. API Security and Reliability

The Camera API must support:

1. Authentication for remote camera access.
2. Network access restrictions.
3. HTTPS or an appropriately secured local network transport.
4. Limits on concurrent viewers.
5. Validation of camera configuration.
6. Safe handling of client disconnects.
7. Cleanup on server shutdown.
8. Bounded retry behavior after camera errors.
9. Avoidance of unbounded frame queues.
10. Independence from time-critical robot movement control.

Because a browser's `<img>` element cannot conveniently attach arbitrary authorization headers, choose a browser-compatible authentication method before implementing the VR integration.

Do not expose an unauthenticated camera stream to the public internet.

---

## 12. API Endpoint Summary

| Method | Endpoint                  | Purpose                      | Response     |
| ------ | ------------------------- | ---------------------------- | ------------ |
| GET    | `/api/v1/camera/stream`   | Live camera feed             | MJPEG stream |
| GET    | `/api/v1/camera/snapshot` | Latest valid image           | JPEG         |
| GET    | `/api/v1/camera/status`   | Camera health and statistics | JSON         |

### Optional Future Endpoints

These are not required for the initial implementation.

| Method | Endpoint                | Purpose                         |
| ------ | ----------------------- | ------------------------------- |
| POST   | `/api/v1/camera/start`  | Explicitly start camera capture |
| POST   | `/api/v1/camera/stop`   | Explicitly stop camera capture  |
| PATCH  | `/api/v1/camera/config` | Update supported settings       |

For the initial implementation, automatic camera lifecycle management is preferred over exposing start/stop controls.

---

## 13. Acceptance Criteria

- [ ] Camera capture works on the Raspberry Pi.
- [ ] The camera device is initialized only once per capture owner.
- [ ] The API streams valid MJPEG frames.
- [ ] The snapshot endpoint returns a valid JPEG image.
- [ ] The status endpoint reports camera state accurately.
- [ ] Camera settings are configurable.
- [ ] Multiple viewers can access the same captured frames.
- [ ] Disconnecting a viewer does not stop capture for other viewers.
- [ ] Failed capture and encoding operations are handled.
- [ ] Camera resources are released on shutdown.
- [ ] The laptop can access the Raspberry Pi camera stream.
- [ ] The Meta Quest 3S browser can display the feed.
- [ ] Camera streaming does not block robot movement commands.
- [ ] Unauthorized camera access is rejected.
- [ ] API classes and methods have useful docstrings and comments.
- [ ] The camera module remains independent of the robot-control API.
- [ ] The APIs should have detailed comments

---

## 14. Out of Scope

The following are not part of the initial Camera Feed API:

- Robot movement and joystick APIs
- Robot posture and actions
- Robot telemetry
- AI image recognition and object detection
- Camera recording and long-term video storage
- Autonomous navigation
- Mission authorization logic
- Barry's three operating modes
- WebRTC implementation
- VR interface design beyond verifying stream compatibility

These features may consume the Camera API later without requiring changes to its core capture interface.
