using Barry.Api;

namespace Barry.UI
{
    public enum StatusTone
    {
        Neutral,
        Ok,
        Accent,
        Warning,
        Danger
    }

    public readonly struct HeaderChipModel
    {
        public readonly string Text;
        public readonly StatusTone Tone;

        public HeaderChipModel(string text, StatusTone tone)
        {
            Text = text;
            Tone = tone;
        }
    }

    public sealed class HeaderChipsModel
    {
        public HeaderChipModel Robot { get; }
        public HeaderChipModel Control { get; }

        private HeaderChipsModel(HeaderChipModel robot, HeaderChipModel control)
        {
            Robot = robot;
            Control = control;
        }

        public static HeaderChipsModel Create(StatusResponse status, bool hasControl)
        {
            bool connected = status != null && status.connected;
            var robot = connected
                ? new HeaderChipModel("ROBOT CONNECTED", StatusTone.Ok)
                : new HeaderChipModel("ROBOT DISCONNECTED", StatusTone.Danger);

            HeaderChipModel control;
            if (hasControl)
            {
                control = new HeaderChipModel("CONTROL: YOU", StatusTone.Accent);
            }
            else if (status != null && status.control_session_active)
            {
                control = new HeaderChipModel("CONTROL: OTHER OPERATOR", StatusTone.Warning);
            }
            else
            {
                control = new HeaderChipModel("CONTROL: FREE", StatusTone.Ok);
            }

            return new HeaderChipsModel(robot, control);
        }
    }
}
