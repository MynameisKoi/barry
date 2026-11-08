using System;
using System.Collections.Generic;
using Barry.Teleop;

namespace Barry.UI
{
    public enum StopOverlayTone
    {
        Danger,
        Warning
    }

    public readonly struct StopOverlayStep
    {
        public int Number { get; }
        public string Text { get; }

        public StopOverlayStep(int number, string text)
        {
            Number = number;
            Text = text;
        }
    }

    public sealed class StopOverlayModel
    {
        public const string StopAcceptedLine = "STOP sent · accepted by the backend";
        public const string StopUndeliveredLine =
            "STOP couldn’t be delivered · Barry’s on-board watchdog halts it when commands stop";

        public string Chip { get; }
        public StopOverlayTone Tone { get; }
        public string Title { get; }
        public string Detail { get; }
        public bool StopDelivered { get; }
        public string StopLine { get; }
        public IReadOnlyList<StopOverlayStep> Steps { get; }
        public string PrimaryActionId { get; }
        public string PrimaryActionLabel { get; }
        public bool PrimaryActionEnabled { get; }

        private StopOverlayModel(
            string chip,
            StopOverlayTone tone,
            string title,
            string detail,
            bool stopDelivered,
            string[] steps,
            string primaryActionId,
            string primaryActionLabel,
            bool primaryActionEnabled)
        {
            Chip = chip;
            Tone = tone;
            Title = title;
            Detail = detail;
            StopDelivered = stopDelivered;
            StopLine = stopDelivered ? StopAcceptedLine : StopUndeliveredLine;
            Steps = NumberSteps(steps);
            PrimaryActionId = primaryActionId;
            PrimaryActionLabel = primaryActionLabel;
            PrimaryActionEnabled = primaryActionEnabled;
        }

        public static StopOverlayModel From(StopReason reason, bool stopDelivered)
        {
            switch (reason)
            {
                case StopReason.ControllerLost:
                    return CreateDanger(
                        "Right controller lost",
                        "The right controller stopped tracking, so Barry was stopped.",
                        stopDelivered,
                        new[]
                        {
                            "Bring the controller back into view, or wake it.",
                            "Centre both sticks.",
                            "Push a stick to drive again."
                        },
                        "wait_for_controller",
                        "Waiting for controller…",
                        false);

                case StopReason.LinkUnreliable:
                    return CreateDanger(
                        "Unreliable link",
                        "Three movement commands timed out (over 300 ms), so Barry was stopped.",
                        stopDelivered,
                        new[]
                        {
                            "Check Wi-Fi between the laptop and Barry.",
                            "Wait for the link to turn green.",
                            "Centre both sticks, then push to drive."
                        },
                        "retry_link",
                        "Retry now");

                case StopReason.BackendUnreachable:
                case StopReason.StatusUnavailable:
                    return CreateDanger(
                        "Backend unreachable",
                        "The headset can’t reach the laptop backend.",
                        stopDelivered,
                        new[]
                        {
                            "Check the backend is running on the laptop.",
                            "Check the headset and laptop are on the same network.",
                            "Take control again after reconnecting."
                        },
                        "retry_connection",
                        "Retry connection");

                case StopReason.ControlDenied:
                case StopReason.ControlExpired:
                    return CreateDanger(
                        "Control expired",
                        "Your control session ended, so Barry was stopped.",
                        stopDelivered,
                        new[]
                        {
                            "Take control again.",
                            "Centre both sticks, then push to drive."
                        },
                        "take_control_again",
                        "Take control again");

                case StopReason.CameraUnavailable:
                    return new StopOverlayModel(
                        "DRIVING PAUSED",
                        StopOverlayTone.Warning,
                        "Camera unavailable",
                        "The camera feed stopped. Driving is paused because you can’t see where Barry is going.",
                        stopDelivered,
                        new[]
                        {
                            "Wait for the feed to return (retrying).",
                            "Centre both sticks, then push to drive."
                        },
                        "retry_camera",
                        "Retry camera",
                        true);

                case StopReason.AppPaused:
                case StopReason.FocusLost:
                case StopReason.XrSessionEnded:
                    // Provisional copy: this lifecycle-interruption variant is not
                    // present in Overlays.dc.html and still needs Tom's sign-off.
                    return CreateDanger(
                        "Stopped",
                        "The headset app was paused, lost focus, or its XR session ended, so Barry was stopped.",
                        stopDelivered,
                        new[]
                        {
                            "Centre both sticks.",
                            "Push a stick to drive again."
                        },
                        "dismiss",
                        "Dismiss");

                case StopReason.Operator:
                    return CreateDanger(
                        "Stopped by you",
                        "You pressed STOP. Barry is holding still.",
                        stopDelivered,
                        new[]
                        {
                            "Centre both sticks.",
                            "Push a stick to drive again."
                        },
                        "dismiss",
                        "Dismiss");

                default:
                    throw new ArgumentOutOfRangeException(nameof(reason), reason, "No stop overlay is defined for this reason.");
            }
        }

        public static bool TryFrom(StopReason reason, bool stopDelivered, out StopOverlayModel model)
        {
            if (reason == StopReason.None || reason == StopReason.SticksCentered)
            {
                model = null;
                return false;
            }

            model = From(reason, stopDelivered);
            return true;
        }

        private static StopOverlayModel CreateDanger(
            string title,
            string detail,
            bool stopDelivered,
            string[] steps,
            string actionId,
            string actionLabel,
            bool actionEnabled = true) =>
            new StopOverlayModel(
                "STOPPED",
                StopOverlayTone.Danger,
                title,
                detail,
                stopDelivered,
                steps,
                actionId,
                actionLabel,
                actionEnabled);

        private static IReadOnlyList<StopOverlayStep> NumberSteps(string[] steps)
        {
            var numbered = new StopOverlayStep[steps.Length];
            for (int index = 0; index < steps.Length; index++)
                numbered[index] = new StopOverlayStep(index + 1, steps[index]);
            return Array.AsReadOnly(numbered);
        }
    }
}
