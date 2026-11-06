# Yahboom Existing Network Remote Protocol

**Source:** `app_dogzilla.py`. This is a source analysis, **not** a validated protocol implementation or a security endorsement.

## Network services

- TCP robot-control server: port **6000** (`start_tcp_server` launched by `init_tcp_socket`).
- Flask video application: `/video_feed` served by gevent WSGI server on port **6500**.
- Video is a separate camera concern and is not implemented inside Barry's robot adapter.

## Packet handling observed

`parse_data(data)` expects an ASCII hexadecimal command string and performs:
1. Minimum-length check.
2. A length check using characters at `[5:7]`.
3. A byte-sum checksum verification.
4. Command dispatch based on characters `[3:5]`.

The parser uses fixed string offsets; `start_tcp_server` calls `recv(1024).decode('utf-8')`. TCP is a byte stream, so a robust compatibility client/server must handle incomplete and coalesced frames explicitly. Do **not** assume one `recv` equals one complete command.

## Observed command IDs

| ID (hex) | Source behavior |
|---|---|
| `0F` | Mode/page selection and state responses |
| `02` | Battery response |
| `10` | Control-page info request |
| `11` | Joystick XY movement |
| `12` | Direction buttons: 1 forward, 2 backward, 3 left, 4 right, 5 turn left, 6 turn right, 7 reset, 0 stop |
| `13` | Step-control setting, clamped 20…100 |
| `14` | Pace mode 1 slow, 2 normal, 3 high; may also change height |
| `15` | IMU self-stabilization toggle |
| `20` | Posture-page info request |
| `21` | Roll/pitch posture |
| `22` | Height control, conditional on pace; source clamps to 110 |
| `23` | Body yaw / shoulder control |
| `31` | Action request; 0 reset, 20 special press-up handling |
| `32` | Cyclic action mode |
| `33` | Reset / leg reset |
| `41` | Individual motor control (exclude from public API) |
| `51` | Individual leg control (exclude from public API) |
| `AA` | Calibration (exclude from public API) |

The exact frame prefix, framing boundaries, and response semantics should be verified by reading the full sender and receiver code before writing a production encoder.

## Architectural choice

**Preferred:** Barry laptop `remote.py` → authenticated Barry Pi `server.py` → `dogzilla.py` → SDK.

**Optional compatibility:** Barry `remote.py` → Yahboom TCP 6000 → `app_dogzilla.py` → SDK.

Never run both as independent serial owners. The supplied legacy application does not demonstrate Barry's required operator authentication, lease, command expiry, or Pi watchdog. Do not expose it directly to the public internet.

## Source-specific concerns

- `app_dogzilla.py` mixes socket transport, movement logic, global control state, camera, and auxiliary hardware.
- Button command `12` calls `back(step)` twice for direction 2; preserve awareness of this source behavior rather than assuming each input maps to one SDK call.
- Pace command `14` also adjusts Z height.
- Raw servo and calibration commands are present and must be filtered from Barry's public surface.
