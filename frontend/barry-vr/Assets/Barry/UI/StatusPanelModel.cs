using System;
using System.Globalization;
using Barry.Api;
using Barry.Teleop;

namespace Barry.UI
{
    public readonly struct StatusValueModel
    {
        public readonly string Text;
        public readonly StatusTone Tone;

        public StatusValueModel(string text, StatusTone tone = StatusTone.Neutral)
        {
            Text = text;
            Tone = tone;
        }
    }

    public readonly struct CommandBarModel
    {
        public readonly string Label;
        public readonly float Value;
        public readonly float FillStart;
        public readonly float FillEnd;
        public readonly string ValueText;

        public CommandBarModel(string label, float value)
        {
            Label = label;
            Value = Clamp(Sanitize(value), -1f, 1f);
            FillStart = 0.5f + Math.Min(Value, 0f) * 0.5f;
            FillEnd = 0.5f + Math.Max(Value, 0f) * 0.5f;
            ValueText = FormatSigned(Value);
        }

        private static float Sanitize(float value) =>
            float.IsNaN(value) || float.IsInfinity(value) ? 0f : value;

        private static float Clamp(float value, float minimum, float maximum) =>
            Math.Max(minimum, Math.Min(maximum, value));

        private static string FormatSigned(float value)
        {
            if (value == 0f) return "0.00";
            string magnitude = Math.Abs(value).ToString("0.00", CultureInfo.InvariantCulture);
            return value < 0f ? "−" + magnitude : magnitude;
        }
    }

    public sealed class StatusPanelModel
    {
        public const string Footer = BarryApiClient.AcceptedDisclaimer;

        public StatusValueModel Robot { get; }
        public StatusValueModel Control { get; }
        public StatusValueModel CameraFps { get; }
        public StatusValueModel BackendRtt { get; }
        public StatusValueModel Battery { get; }
        public float BatteryFill { get; }
        public CommandBarModel Forward { get; }
        public CommandBarModel Strafe { get; }
        public CommandBarModel Turn { get; }

        private StatusPanelModel(
            StatusValueModel robot,
            StatusValueModel control,
            StatusValueModel cameraFps,
            StatusValueModel backendRtt,
            StatusValueModel battery,
            float batteryFill,
            MotionCommand command)
        {
            Robot = robot;
            Control = control;
            CameraFps = cameraFps;
            BackendRtt = backendRtt;
            Battery = battery;
            BatteryFill = batteryFill;
            Forward = new CommandBarModel("Forward", command.Forward);
            Strafe = new CommandBarModel("Strafe", command.Strafe);
            Turn = new CommandBarModel("Turn", command.Turn);
        }

        public static StatusPanelModel Create(
            StatusResponse status,
            TelemetryResponse telemetry,
            CameraStatusResponse camera,
            MotionCommand command,
            bool hasControl,
            float? backendRttMs = null)
        {
            bool connected = status != null && status.connected;
            var robot = connected
                ? new StatusValueModel("Connected", StatusTone.Ok)
                : new StatusValueModel("Disconnected", StatusTone.Danger);

            StatusValueModel control;
            if (hasControl)
                control = new StatusValueModel("You", StatusTone.Accent);
            else if (status != null && status.control_session_active)
                control = new StatusValueModel("Other operator", StatusTone.Warning);
            else
                control = new StatusValueModel("Free", StatusTone.Ok);

            var cameraFps = new StatusValueModel(FormatFps(camera));
            var backendRtt = new StatusValueModel(FormatMilliseconds(backendRttMs));

            // Backend battery is Optional<float>, but JsonUtility maps JSON null to 0.
            // Treat non-positive values as unknown. Values are assumed to be percent;
            // that unit still needs verification against the physical robot.
            bool hasBattery = telemetry != null && IsFinite(telemetry.battery) && telemetry.battery > 0f;
            float batteryPercent = hasBattery ? Clamp(telemetry.battery, 0f, 100f) : 0f;
            var battery = new StatusValueModel(
                hasBattery ? batteryPercent.ToString("0", CultureInfo.InvariantCulture) + " %" : "-- %",
                BatteryTone(hasBattery, batteryPercent));

            return new StatusPanelModel(
                robot, control, cameraFps, backendRtt, battery,
                batteryPercent / 100f, command);
        }

        private static string FormatFps(CameraStatusResponse camera)
        {
            if (camera == null || !camera.connected || !IsFinite(camera.actual_fps) || camera.actual_fps <= 0f)
                return "-- fps";
            return camera.actual_fps.ToString("0.0", CultureInfo.InvariantCulture) + " fps";
        }

        private static string FormatMilliseconds(float? value)
        {
            if (!value.HasValue || !IsFinite(value.Value) || value.Value < 0f) return "-- ms";
            return value.Value.ToString("0", CultureInfo.InvariantCulture) + " ms";
        }

        private static StatusTone BatteryTone(bool hasBattery, float percent)
        {
            if (!hasBattery) return StatusTone.Neutral;
            if (percent <= 15f) return StatusTone.Danger;
            if (percent <= 30f) return StatusTone.Warning;
            return StatusTone.Neutral;
        }

        private static bool IsFinite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        private static float Clamp(float value, float minimum, float maximum) =>
            Math.Max(minimum, Math.Min(maximum, value));
    }
}
