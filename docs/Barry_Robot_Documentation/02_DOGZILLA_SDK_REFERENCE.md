# DOGZILLALib.py — Source-Grounded Reference

**Source:** uploaded `DOGZILLALib.py`, version label V2.0.7. Its `DOGZILLA` class defaults to serial port `/dev/ttyAMA0` and 115200 baud. Hardware compatibility with the specific DOGZILLA S2 controller must be verified.

## Object-oriented structure

`DOGZILLA` owns a `serial.Serial` object (`self.ser`). Public methods update command data and call private `__send`; responses use `__read`/`__unpack`. Module-level mutable `ORDER` state means independent concurrent callers can interfere; serialize SDK operations.

## Movement

| Methods | Behavior / source constraints |
|---|---|
| `forward(step)`, `back(step)` | Positive/negative X movement |
| `left(step)`, `right(step)` | Positive/negative Y movement |
| `turnleft(step)`, `turnright(step)` | Positive/negative yaw command |
| `move('x'/'y', step)` | Dispatches to X/Y |
| `move_x(step)` | Clamps to -20…20 |
| `move_y(step)` | Clamps to -18…18 |
| `turn(step)` | Clamps to -70…70; nonzero magnitude below 30 is raised to 30 |
| `stop()` | Sets X/Y movement, marking time, and turn to zero |

`stop()` **does not** call `pace()`, reset the body posture, or explicitly disable periodic rotation/translation.

## Posture

- `translation(direction, data)` accepts X/Y/Z axis or lists; body translates while feet stay in place.
- `attitude(direction, data)` accepts `r` (roll), `p` (pitch), `y` (yaw) or lists.
- SDK uses its own `PARAM` limits; verify presets against actual hardware.
- `reset()` invokes `action(255)` and is documented as stopping motion and restoring initial parameters; it is broader than a posture-only reset.

## Movement style

- `pace('normal'|'slow'|'high')`: stepping frequency, **not guaranteed linear speed**.
- `gait_type('trot'|'walk'|'high_walk')`: gait selection.
- The `gait_type` method does not have an explicit `else` for invalid modes; validate before calling.

Barry mapping: low→slow, medium→normal, high→high. Store selected intensity in Barry state and preserve it when stopping.

## Actions and periodic behavior

- `action(action_id)`: IDs 1…255; 255 used by reset. No full action-name catalog in the SDK.
- `perform(0|1)`: disables/enables cyclic action mode.
- `periodic_rot('r'|'p'|'y', period)`: periodic body rotation; 0 disables selected axis.
- `periodic_tran('x'|'y'|'z', period)`: periodic body translation; 0 disables selected axis.
- `mark_time(data)`: step in place; 0 disables.
- Period limit comes from SDK `PARAM`; its unit and physical amplitude are not clearly specified in the supplied source.

## Telemetry

- `read_battery()`, `read_version()`
- `read_roll(out_int=False)`, `read_pitch(out_int=False)`, `read_yaw(out_int=False)`
- `read_motor(out_int=False)` for motor readings

Do not confuse failed/zero readings with confirmed physical measurements. Distinguish telemetry from application-tracked commands.

## Restricted hardware methods

`motor`, `leg`, `motor_speed`, `load_motor`, `unload_motor`, `load_allmotor`, `unload_allmotor`, `calibration`, and `imu` should not be exposed as unrestricted Quest controls. `motor_speed` is for individual servo control, not walking pace.

## Integration note

Import `DOGZILLA` only on the Pi when real mode is selected. Prefer an importable installed module (`from DOGZILLALib import DOGZILLA`) rather than hardcoding a source-file path. No hardware import is needed for simulation.
