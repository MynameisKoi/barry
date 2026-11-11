using System.Collections.Generic;
using System.Globalization;
using Barry.Api;
using Barry.Teleop;

namespace Barry.UI
{
    public readonly struct LlmPreset
    {
        public readonly string Label;   // button text
        public readonly string Goal;    // sent to POST /llm/plan

        public LlmPreset(string label, string goal)
        {
            Label = label;
            Goal = goal;
        }
    }

    /// <summary>The preset goals, in one list so they're easy to edit. Typing in VR is poor; voice comes in #93.</summary>
    public static class LlmPresets
    {
        public static readonly LlmPreset[] All =
        {
            new LlmPreset("Look around and tell me what you see", "Look and tell me what you see"),
            new LlmPreset("Walk forward 2 seconds", "Walk forward 2 seconds"),
            new LlmPreset("Turn right 90°", "Turn right 90 degrees"),
            new LlmPreset("Check status", "Check the status"),
            new LlmPreset("What can you do?", "What can you do?"),
        };
    }

    public readonly struct LlmStepRow
    {
        public readonly string Text;
        public readonly string StateWord;
        public readonly StatusTone Tone;

        public LlmStepRow(string text, string stateWord, StatusTone tone)
        {
            Text = text;
            StateWord = stateWord;
            Tone = tone;
        }
    }

    /// <summary>What the brain panel shows, derived from the monitor. State is always a word plus a tone.</summary>
    public sealed class LlmPanelModel
    {
        public const int MaxRows = 6;
        public const string ReplacesNotice = "Starting a new task cancels the current one.";
        public const string CancelLabel = "Cancel";
        public const string CancellingLabel = "Cancelling…";

        public bool Open { get; private set; }
        public HeaderChipModel Chip { get; private set; } = new HeaderChipModel("IDLE", StatusTone.Neutral);
        public IReadOnlyList<LlmStepRow> Rows { get; private set; } = new LlmStepRow[0];
        public int HiddenSteps { get; private set; }
        public string AnswerText { get; private set; } = string.Empty;
        public StatusTone AnswerTone { get; private set; } = StatusTone.Neutral;
        public bool CancelVisible { get; private set; }
        public bool CancelEnabled { get; private set; }
        public string CancelText { get; private set; } = CancelLabel;
        public bool PresetsEnabled { get; private set; } = true;
        public string Notice { get; private set; } = string.Empty;

        public void Toggle() => Open = !Open;
        public void SetOpen(bool open) => Open = open;

        public void Update(LlmTaskMonitor monitor)
        {
            LlmTaskStatus task = monitor?.Latest;
            bool running = monitor != null && monitor.IsRunning;

            PresetsEnabled = monitor != null && !monitor.Starting;
            CancelVisible = running;
            CancelEnabled = running && !monitor.Cancelling;
            CancelText = monitor != null && monitor.Cancelling ? CancellingLabel : CancelLabel;
            Notice = running || (monitor != null && monitor.ReplacedPrevious && monitor.Starting) ? ReplacesNotice : string.Empty;

            Chip = ChipFor(monitor, task);
            BuildRows(task);

            if (monitor != null && !string.IsNullOrEmpty(monitor.Error))
            {
                AnswerText = monitor.Error;
                AnswerTone = StatusTone.Danger;
                return;
            }

            AnswerText = string.Empty;
            AnswerTone = StatusTone.Neutral;
            if (task == null) return;
            switch (task.state)
            {
                case "done":
                    AnswerText = string.IsNullOrWhiteSpace(task.answer) ? "Done." : task.answer;
                    AnswerTone = StatusTone.Ok;
                    break;
                case "cancelled":
                    AnswerText = Or(task.message, "The task was cancelled.");
                    AnswerTone = StatusTone.Warning;
                    break;
                case "limit":
                    AnswerText = Or(task.message, "A limit was reached.");
                    AnswerTone = StatusTone.Warning;
                    break;
                case "failed":
                    AnswerText = Or(task.message, "The task failed.");
                    AnswerTone = StatusTone.Danger;
                    break;
                case "refused":
                    AnswerText = Or(task.message, "The assistant declined this request.");
                    AnswerTone = StatusTone.Danger;
                    break;
            }
        }

        private static string Or(string value, string fallback) => string.IsNullOrWhiteSpace(value) ? fallback : value;

        private static HeaderChipModel ChipFor(LlmTaskMonitor monitor, LlmTaskStatus task)
        {
            if (monitor != null && monitor.Starting) return new HeaderChipModel("STARTING…", StatusTone.Accent);
            if (task == null) return new HeaderChipModel("IDLE", StatusTone.Neutral);
            switch (task.state)
            {
                case "running":
                    return new HeaderChipModel("RUNNING · TURN " + task.turns.ToString(CultureInfo.InvariantCulture), StatusTone.Accent);
                case "done": return new HeaderChipModel("DONE", StatusTone.Ok);
                case "cancelled": return new HeaderChipModel("CANCELLED", StatusTone.Warning);
                case "limit": return new HeaderChipModel("LIMIT REACHED", StatusTone.Warning);
                case "failed": return new HeaderChipModel("FAILED", StatusTone.Danger);
                case "refused": return new HeaderChipModel("REFUSED", StatusTone.Danger);
                default: return new HeaderChipModel("IDLE", StatusTone.Neutral);
            }
        }

        private void BuildRows(LlmTaskStatus task)
        {
            var rows = new List<LlmStepRow>();
            HiddenSteps = 0;
            if (task?.steps != null)
            {
                int first = System.Math.Max(0, task.steps.Length - MaxRows);   // newest steps stay in view
                HiddenSteps = first;
                for (int i = first; i < task.steps.Length; i++)
                {
                    LlmStep step = task.steps[i];
                    if (step == null) continue;
                    string label = (i + 1).ToString(CultureInfo.InvariantCulture) + ". " + step.tool;
                    if (!string.IsNullOrEmpty(step.args_summary)) label += " " + step.args_summary;
                    rows.Add(new LlmStepRow(label, StateWord(step), StateTone(step)));
                }
            }
            Rows = rows;
        }

        /// <summary>A step that ran but whose gateway result was a refusal (MISSION_REQUIRED, ESTOP_ACTIVE, BUSY, OFFLINE...).
        /// ACCEPTED and STOP_DISPATCHED are the normal successes.</summary>
        public static bool IsRefused(LlmStep step) =>
            step != null && step.state == "done" && !string.IsNullOrEmpty(step.result_status)
            && step.result_status != "ACCEPTED" && step.result_status != "STOP_DISPATCHED";

        private static string StateWord(LlmStep step)
        {
            if (IsRefused(step)) return step.result_status;   // shown as the word, e.g. MISSION_REQUIRED
            switch (step.state)
            {
                case "running": return "running…";
                case "done": return "done";
                case "rejected": return "rejected";
                case "failed": return "failed";
                case "skipped": return "skipped";
                default: return step.state ?? "?";
            }
        }

        private static StatusTone StateTone(LlmStep step)
        {
            if (IsRefused(step)) return StatusTone.Warning;
            switch (step.state)
            {
                case "running": return StatusTone.Accent;
                case "done": return StatusTone.Ok;
                case "rejected": return StatusTone.Warning;
                case "failed": return StatusTone.Danger;
                default: return StatusTone.Neutral;
            }
        }
    }
}
