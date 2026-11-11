using System.Globalization;
using Barry.Teleop;

namespace Barry.UI
{
    /// <summary>
    /// What the latched-e-stop HUD shows. The word "E-STOP LATCHED" always accompanies the danger tone
    /// (never colour alone). Reset takes two deliberate presses; it never moves Barry.
    /// </summary>
    public sealed class EmergencyHudModel
    {
        public const string ChipText = "E-STOP LATCHED";
        public const string Title = "Emergency stop latched";
        public const string Detail = "Barry will not move until the e-stop is reset.";
        public const string Footer = "Reset never moves Barry: centre both sticks, then push to drive.";

        public bool Visible { get; }
        public string Chip { get; }
        public StatusTone Tone { get; }
        public string TitleText { get; }
        public string DetailText { get; }
        public string ButtonLabel { get; }
        public bool ButtonEnabled { get; }
        public string FooterText { get; }

        private EmergencyHudModel(bool visible, string detail, string buttonLabel, bool buttonEnabled)
        {
            Visible = visible;
            Chip = visible ? ChipText : string.Empty;
            Tone = StatusTone.Danger;
            TitleText = visible ? Title : string.Empty;
            DetailText = detail;
            ButtonLabel = buttonLabel;
            ButtonEnabled = buttonEnabled;
            FooterText = visible ? Footer : string.Empty;
        }

        public static EmergencyHudModel From(EmergencyLatch latch)
        {
            if (latch == null || !latch.IsLatched)
                return new EmergencyHudModel(false, string.Empty, string.Empty, false);

            switch (latch.Phase)
            {
                case ResetPhase.Confirming:
                    int seconds = (int)System.Math.Ceiling(latch.ConfirmSecondsLeft);
                    return new EmergencyHudModel(true,
                        "Make sure Barry and the area around it are safe, then press again to confirm.",
                        "Press again to confirm (" + seconds.ToString(CultureInfo.InvariantCulture) + " s)",
                        true);
                case ResetPhase.Resetting:
                    return new EmergencyHudModel(true, Detail, "Resetting…", false);
                default:
                    string detail = string.IsNullOrEmpty(latch.LastError) ? Detail : latch.LastError + " " + Detail;
                    return new EmergencyHudModel(true, detail, "Reset e-stop", true);
            }
        }
    }
}
