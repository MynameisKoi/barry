# Yahboom Joystick Reference and Barry Mapping

**Source:** `joystick_dogzilla.py`, class `Dogzilla_Joystick` (V2.0.7).

This class reads a **local Linux input device** `/dev/input/jsN` as 8-byte joystick events. It accepts a `DOGZILLA` instance and calls SDK methods. It is **not** a network protocol and should not be imported into the Quest or laptop remote client.

## Physical controls

| Yahboom input | Observed SDK behavior |
|---|---|
| RK1 horizontal | `move('y', scaled value)` (strafe) |
| RK1 vertical | `move('x', scaled value)` (forward/backward) |
| RK2 horizontal | `turn(...)`; notably only sends nonzero turn at full ±1 in this code |
| RK2 vertical | `attitude('p', value)` (body pitch) |
| A | Reduce height by 10, minimum 75, when pace state permits |
| Y | Increase height by 10, maximum 105, when pace state permits |
| B / X | Set body yaw to -11 / +11 on press; reset yaw on release |
| L1 | `action(10)` (three-axis action, per source comment) |
| R1 | Starts custom ball-playing motor sequence |
| L2 | `action(16)` (swing, per comment) at full trigger |
| R2 | `action(11)` (pee, per comment) at full trigger |
| START | `reset()` and internal control-state reset |
| RK1 stick button | Cycles step-control values |
| RK2 stick button | Cycles slow/normal/high pace; slow also sets height 75 |
| Direction pad | Additional X/Y movement |

## Source scaling

- `__STEP_SCALE_X = 0.2`
- `__STEP_SCALE_Y = 0.2`
- `__STEP_SCALE_Z = 0.7`
- Initial `__step_control = 70`
- Initial `__pace_freq = 2` (normal-state index)
- Initial `__height = 105`

These are **reference implementation settings**, not certified physical limits or speed measurements.

## Recommended Barry joystick abstraction

```json
{
  "forward": 0.5,
  "strafe": -0.2,
  "turn": 0.0
}
```

All axes normalized to [-1, 1]. Apply dead zone, clamping, calibrated scaling, and Pi-side timeout. A Quest joystick does not need to simulate `/dev/input/js0`.

Do not copy the source's custom ball-playing raw motor sequence into the standard API. Expose only vetted preset actions.

## Important distinctions

- Physical joystick event handling is **input adaptation**.
- `remote.py` is **network transport**.
- `dogzilla.py` is **hardware adaptation**.
- `interface.py` defines shared **robot capabilities**.

The Yahboom source combines height and slow pace; Barry should make this policy explicit rather than silently coupling two settings.
