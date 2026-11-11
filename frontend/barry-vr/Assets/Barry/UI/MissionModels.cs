using System;
using System.Globalization;
using Barry.Api;
using Barry.Teleop;

namespace Barry.UI
{
    /// <summary>Header chip text for the mission: "MISSION · zone · operator" (word + colour), or NO MISSION.</summary>
    public static class MissionChipModel
    {
        public const string NoMissionText = "NO MISSION";
        public const string UnknownText = "MISSION · ?";

        public static HeaderChipModel From(MissionStatus status)
        {
            if (status == null) return new HeaderChipModel(UnknownText, StatusTone.Neutral);
            if (!status.active) return new HeaderChipModel(NoMissionText, StatusTone.Warning);
            string zone = string.IsNullOrWhiteSpace(status.zone) ? "?" : status.zone;
            string who = string.IsNullOrWhiteSpace(status.operator_name) ? status.operator_id : status.operator_name;
            return new HeaderChipModel("MISSION · " + zone + " · " + (string.IsNullOrWhiteSpace(who) ? "?" : who), StatusTone.Accent);
        }
    }

    /// <summary>
    /// The start-mission card: operator picker (from GET /api/operators), zone presets, Start. It is shown
    /// exactly when the backend says no mission is active; when the status is unknown (backend unreachable)
    /// it is hidden rather than guessing. Motion is gated by missions, stop/e-stop never are.
    /// </summary>
    public sealed class MissionPanelModel
    {
        public const string DefaultZone = "Zone-A";
        public static readonly string[] ZonePresets = { "Zone-A", "Zone-B", "Zone-C", "Zone-D" };
        public const string Title = "Start a mission";
        public const string MissionRequiredHint = "Start a mission to drive";
        public const string StartLabel = "Start mission";
        public const string StartingLabel = "Starting…";

        private int operatorIndex;
        private int zoneIndex;

        public MissionPanelModel()
        {
            zoneIndex = Array.IndexOf(ZonePresets, DefaultZone);
        }

        public bool Visible { get; private set; }
        public OperatorInfo[] Operators { get; private set; }
        public string OperatorsError { get; private set; }
        public bool Starting { get; private set; }
        public string StartError { get; private set; }
        /// <summary>Shown after the gateway refused motion; cleared once a mission is active or Start is pressed.</summary>
        public bool ShowMissionRequiredHint { get; private set; }

        public OperatorInfo SelectedOperator =>
            Operators != null && Operators.Length > 0 ? Operators[operatorIndex] : null;
        public string SelectedZone => ZonePresets[zoneIndex];
        public bool HasOperators => Operators != null && Operators.Length > 0;

        public string OperatorText
        {
            get
            {
                if (Operators == null) return string.IsNullOrEmpty(OperatorsError) ? "Loading operators…" : OperatorsError;
                if (Operators.Length == 0) return "No operators registered";
                OperatorInfo op = Operators[operatorIndex];
                return string.IsNullOrWhiteSpace(op.role) ? op.name : op.name + " · " + op.role;
            }
        }

        public bool StartEnabled => Visible && !Starting && SelectedOperator != null;
        public string StartButtonLabel => Starting ? StartingLabel : StartLabel;
        public string HintText => ShowMissionRequiredHint ? MissionRequiredHint : string.Empty;

        /// <summary>Recompute from the monitor. Call every time the monitor changes or the hint toggles.</summary>
        public void Update(MissionMonitor monitor)
        {
            if (monitor == null) throw new ArgumentNullException(nameof(monitor));
            Visible = monitor.StatusKnown && !monitor.IsActive;
            SetOperators(monitor.Operators, monitor.OperatorsError);
            Starting = monitor.Starting;
            StartError = monitor.StartError;
            if (monitor.IsActive) ShowMissionRequiredHint = false;
        }

        public void SetOperators(OperatorInfo[] operators, string error = null)
        {
            string previousId = SelectedOperator?.operator_id;
            Operators = operators;
            OperatorsError = error;
            operatorIndex = 0;
            if (operators == null || previousId == null) return;
            for (int i = 0; i < operators.Length; i++)
                if (operators[i].operator_id == previousId) operatorIndex = i;   // keep the pick across polls
        }

        public void CycleOperator(int step)
        {
            if (!HasOperators) return;
            operatorIndex = ((operatorIndex + step) % Operators.Length + Operators.Length) % Operators.Length;
        }

        public void CycleZone(int step)
        {
            zoneIndex = ((zoneIndex + step) % ZonePresets.Length + ZonePresets.Length) % ZonePresets.Length;
        }

        public void ShowMissionRequired() => ShowMissionRequiredHint = true;
        public void ClearHint() => ShowMissionRequiredHint = false;
    }

    /// <summary>The End mission button (control strip): two presses, same pattern as the e-stop reset.</summary>
    public sealed class EndMissionModel
    {
        public const string EndLabel = "End mission";

        public bool Visible { get; }
        public string Label { get; }
        public bool Enabled { get; }
        public StatusTone Tone { get; }
        public string Error { get; }

        private EndMissionModel(bool visible, string label, bool enabled, StatusTone tone, string error)
        {
            Visible = visible;
            Label = label;
            Enabled = enabled;
            Tone = tone;
            Error = error;
        }

        public static EndMissionModel From(MissionMonitor monitor)
        {
            if (monitor == null || !monitor.IsActive) return new EndMissionModel(false, EndLabel, false, StatusTone.Neutral, null);
            ConfirmGate gate = monitor.EndGate;
            if (gate.Busy) return new EndMissionModel(true, "Ending…", false, StatusTone.Warning, null);
            if (gate.Armed)
            {
                int seconds = (int)Math.Ceiling(gate.SecondsLeft);
                return new EndMissionModel(true,
                    "Press again to end (" + seconds.ToString(CultureInfo.InvariantCulture) + " s)",
                    true, StatusTone.Danger, null);
            }
            return new EndMissionModel(true, EndLabel, true, StatusTone.Neutral, monitor.EndError);
        }
    }
}
