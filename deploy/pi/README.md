# Pi deployment: BARRY Robot API

This runs the Robot API (`backend/robot/server.py`, port 8100) on the Raspberry Pi that drives the DOGZILLA S2. The laptop relay talks to it over Wi-Fi.

Files in this folder:

- `barry-robot.service`: systemd unit.
- `robot.env.example`: environment file, installed as `/etc/barry/robot.env`. Variable names and defaults come from `backend/robot/config.py`.

## 1. Prerequisites

- Raspberry Pi OS, a Python 3 install (check with `python3 --version`), network access.
- The DOGZILLA S2 powered and connected to the Pi's serial port (`/dev/ttyAMA0` by default).
- Lift the robot on a stand for first tests.

## 2. Stop the vendor service first

The Yahboom image starts its own control program (`app_dogzilla.py`) at boot. Two processes on one serial port will fight over the robot, so stop and disable it before starting BARRY:

```bash
systemctl list-units --all | grep -i -E 'dogzilla|yahboom'   # find the exact unit name
sudo systemctl stop <yahboom-unit>
sudo systemctl disable <yahboom-unit>
```

Only one process should own `DOGZILLA_PORT` after this.

## 3. Fixed address

Give the Pi a fixed DHCP address so the laptop's `ROBOT_REMOTE_URL` never changes:

1. Find the Pi's MAC address (`ip link show wlan0`).
2. In the router's admin page, reserve an IP for that MAC.
3. Check the Pi's hostname (`hostname`). The laptop default is `http://barry-pi.local:8100`, so the hostname must resolve on the laptop, or set `ROBOT_REMOTE_URL` to the reserved IP.

## 4. Install

Copy the project to `/opt/barry` (for example with `rsync` from the laptop, excluding `frontend/` and `.git/`). Then:

```bash
sudo useradd --system --create-home --home-dir /var/lib/barry-robot --shell /usr/sbin/nologin barry-robot
sudo usermod -a -G dialout barry-robot
sudo mkdir -p /etc/barry
sudo chown root:barry-robot /etc/barry
sudo chmod 0750 /etc/barry

cd /opt/barry
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn pydantic pyserial
sudo chown -R barry-robot:barry-robot /opt/barry
```

The install list is the Phase 1 Pi set: FastAPI, uvicorn, pydantic, pyserial. Camera dependencies are added in Phase 4.

Create the environment file:

```bash
sudo install -m 0640 -o root -g barry-robot deploy/pi/robot.env.example /etc/barry/robot.env
sudo nano /etc/barry/robot.env        # set ROBOT_API_KEY (openssl rand -hex 32)
```

Install and start the service:

```bash
sudo install -m 0644 deploy/pi/barry-robot.service /etc/systemd/system/barry-robot.service
sudo systemctl daemon-reload
sudo systemctl enable --now barry-robot
```

## 5. Check it

```bash
systemctl status barry-robot
journalctl -u barry-robot -f
curl -s http://127.0.0.1:8100/healthz
```

`/healthz` needs no key and should return a healthy status. Every other route needs the `X-Robot-Key` header set to `ROBOT_API_KEY`.

Then from the laptop, check the reserved address:

```bash
curl -s http://<pi-address>:8100/healthz
```

## 6. Safety checks before the robot moves

- Lift the robot. Send a short forward move from the Quest; the legs should move.
- Release the stick. The robot should stop within `ROBOT_WATCHDOG_MS` (default 500 ms).
- Turn off the laptop's Wi-Fi during a move. The robot should stop.

## 7. Settings

| Variable | Default | Notes |
|---|---|---|
| `ROBOT_MODE` | `simulator` | Set to `dogzilla` on the Pi. |
| `ROBOT_API_KEY` | none | Required. Shared with the laptop relay. |
| `DOGZILLA_PORT` | `/dev/ttyAMA0` | Serial device. |
| `ROBOT_WATCHDOG_MS` | `500` | Stop if no motion command for this long. |
| `ROBOT_SESSION_TTL_S` | `10` | Idle control session expiry. |
| `ROBOT_MAX_COMMAND_AGE_MS` | `0` | `0` disables. Enable only with NTP on both machines. |

After editing `/etc/barry/robot.env`, run `sudo systemctl restart barry-robot`.

## Troubleshooting

- `Permission denied` on the serial port: `barry-robot` is not in `dialout`, or the vendor service still holds the port.
- Service restarts in a loop: check `journalctl -u barry-robot`. An unknown `ROBOT_MODE` or a non-numeric value in the env file stops startup.
- Laptop gets `401`: the `ROBOT_API_KEY` on the Pi and the laptop differ.
- `/healthz` errors and journalctl shows `ROBOT_API_KEY is required`: set the key in `/etc/barry/robot.env` and restart (`sudo systemctl restart barry-robot`).
