using Barry.Api;
using Barry.Teleop;
using NUnit.Framework;
using System;
using System.Collections;

namespace Barry.UI.Tests
{
    public sealed class MissionModelsTests
    {
        private sealed class StubApi : IMissionApi
        {
            public Action<ApiResponse<MissionStatus>> StatusCb;
            public IEnumerator GetMissionStatus(Action<ApiResponse<MissionStatus>> c) { StatusCb = c; return Empty(); }
            private static IEnumerator Empty() { yield break; }
            public IEnumerator GetOperators(Action<ApiResponse<OperatorsResponse>> c) { yield break; }
            public IEnumerator StartMission(MissionStartRequest b, Action<ApiResponse<MissionStatus>> c) { yield break; }
            public IEnumerator StopMission(string r, Action<ApiResponse<MissionStatus>> c) { yield break; }
        }

        private static MissionMonitor MonitorWith(MissionStatus status, out StubApi api)
        {
            api = new StubApi();
            var monitor = new MissionMonitor(api, _ => { });
            monitor.PollNow();
            if (status != null) api.StatusCb(new ApiResponse<MissionStatus>(true, 200, status, null));
            else api.StatusCb(new ApiResponse<MissionStatus>(false, 0, null, "offline"));
            return monitor;
        }

        private static readonly OperatorInfo[] Two =
        {
            new OperatorInfo { operator_id = "op1", name = "Ada", role = "Inspector" },
            new OperatorInfo { operator_id = "op2", name = "Bo", role = "" }
        };

        // ---- chip

        [Test]
        public void Chip_ActiveMission_ShowsZoneAndOperator_WithAccentTone()
        {
            HeaderChipModel chip = MissionChipModel.From(new MissionStatus { active = true, zone = "Zone-B", operator_name = "Ada", operator_id = "op1" });
            Assert.That(chip.Text, Is.EqualTo("MISSION · Zone-B · Ada"));
            Assert.That(chip.Tone, Is.EqualTo(StatusTone.Accent));
        }

        [Test]
        public void Chip_FallsBackToTheOperatorId_AndQuestionMarks()
        {
            Assert.That(MissionChipModel.From(new MissionStatus { active = true, zone = "Zone-A", operator_id = "op9" }).Text,
                Is.EqualTo("MISSION · Zone-A · op9"));
            Assert.That(MissionChipModel.From(new MissionStatus { active = true }).Text, Is.EqualTo("MISSION · ? · ?"));
        }

        [Test]
        public void Chip_NoMission_SaysSoInWords_WithWarningTone_AndUnknownIsNeutral()
        {
            HeaderChipModel none = MissionChipModel.From(new MissionStatus { active = false });
            Assert.That(none.Text, Is.EqualTo("NO MISSION"));
            Assert.That(none.Tone, Is.EqualTo(StatusTone.Warning));
            Assert.That(MissionChipModel.From(null).Text, Is.EqualTo("MISSION · ?"));
            Assert.That(MissionChipModel.From(null).Tone, Is.EqualTo(StatusTone.Neutral));
        }

        // ---- panel visibility rule

        [Test]
        public void Panel_IsShownOnlyWhenTheBackendSaysNoMissionIsActive()
        {
            var panel = new MissionPanelModel();
            panel.Update(MonitorWith(new MissionStatus { active = false }, out _));
            Assert.That(panel.Visible, Is.True);

            panel.Update(MonitorWith(new MissionStatus { active = true, zone = "Zone-A" }, out _));
            Assert.That(panel.Visible, Is.False);

            panel.Update(MonitorWith(null, out _));      // backend unreachable: unknown, not "no mission"
            Assert.That(panel.Visible, Is.False);
        }

        [Test]
        public void Panel_DefaultsToZoneA_AndCyclesThePresets()
        {
            var panel = new MissionPanelModel();
            Assert.That(panel.SelectedZone, Is.EqualTo("Zone-A"));
            panel.CycleZone(1);
            Assert.That(panel.SelectedZone, Is.EqualTo("Zone-B"));
            panel.CycleZone(-2);
            Assert.That(panel.SelectedZone, Is.EqualTo("Zone-D"));   // wraps
        }

        [Test]
        public void Panel_OperatorPicker_ShowsLoadingEmptyErrorAndTheList()
        {
            var panel = new MissionPanelModel();
            Assert.That(panel.OperatorText, Is.EqualTo("Loading operators…"));
            Assert.That(panel.SelectedOperator, Is.Null);

            panel.SetOperators(null, "offline");
            Assert.That(panel.OperatorText, Is.EqualTo("offline"));

            panel.SetOperators(new OperatorInfo[0]);
            Assert.That(panel.OperatorText, Is.EqualTo("No operators registered"));
            panel.CycleOperator(1);                                  // no crash on an empty list

            panel.SetOperators(Two);
            Assert.That(panel.OperatorText, Is.EqualTo("Ada · Inspector"));
            panel.CycleOperator(1);
            Assert.That(panel.SelectedOperator.operator_id, Is.EqualTo("op2"));
            Assert.That(panel.OperatorText, Is.EqualTo("Bo"));
            panel.CycleOperator(1);
            Assert.That(panel.SelectedOperator.operator_id, Is.EqualTo("op1"));   // wraps
        }

        [Test]
        public void Panel_KeepsTheSelectedOperatorWhenTheListIsRefreshed()
        {
            var panel = new MissionPanelModel();
            panel.SetOperators(Two);
            panel.CycleOperator(1);
            panel.SetOperators(Two);
            Assert.That(panel.SelectedOperator.operator_id, Is.EqualTo("op2"));
        }

        [Test]
        public void Panel_StartNeedsAnOperator_AndIsDisabledWhileStarting()
        {
            var panel = new MissionPanelModel();
            panel.Update(MonitorWith(new MissionStatus { active = false }, out _));
            Assert.That(panel.StartEnabled, Is.False);               // no operators yet
            panel.SetOperators(Two);
            Assert.That(panel.StartEnabled, Is.True);
            Assert.That(panel.StartButtonLabel, Is.EqualTo("Start mission"));
        }

        // ---- MISSION_REQUIRED hint

        [Test]
        public void Hint_AppearsOnMissionRequired_ClearsOnStartOrWhenAMissionBecomesActive()
        {
            var panel = new MissionPanelModel();
            panel.Update(MonitorWith(new MissionStatus { active = false }, out _));
            Assert.That(panel.HintText, Is.Empty);

            panel.ShowMissionRequired();
            Assert.That(panel.HintText, Is.EqualTo("Start a mission to drive"));
            panel.ClearHint();
            Assert.That(panel.HintText, Is.Empty);

            panel.ShowMissionRequired();
            panel.Update(MonitorWith(new MissionStatus { active = true }, out _));
            Assert.That(panel.HintText, Is.Empty);
        }

        [Test]
        public void MissionRequired_HasItsOwnStopOverlay()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.MissionRequired, true);
            Assert.That(model.Title, Is.EqualTo("Start a mission to drive"));
            Assert.That(model.PrimaryActionId, Is.EqualTo("start_mission"));
            Assert.That(StopOverlayModel.TryFrom(StopReason.MissionRequired, false, out _), Is.True);
        }

        // ---- end mission

        [Test]
        public void EndMission_IsHiddenWithoutAMission_ThenTwoPressesWithACountdown()
        {
            Assert.That(EndMissionModel.From(MonitorWith(new MissionStatus { active = false }, out _)).Visible, Is.False);
            Assert.That(EndMissionModel.From(null).Visible, Is.False);

            MissionMonitor monitor = MonitorWith(new MissionStatus { active = true }, out _);
            EndMissionModel idle = EndMissionModel.From(monitor);
            Assert.That(idle.Visible, Is.True);
            Assert.That(idle.Label, Is.EqualTo("End mission"));
            Assert.That(idle.Enabled, Is.True);

            monitor.PressEndMission();
            EndMissionModel armed = EndMissionModel.From(monitor);
            Assert.That(armed.Label, Is.EqualTo("Press again to end (3 s)"));
            Assert.That(armed.Tone, Is.EqualTo(StatusTone.Danger));
            monitor.Tick(1.2f);
            Assert.That(EndMissionModel.From(monitor).Label, Is.EqualTo("Press again to end (2 s)"));

            monitor.PressEndMission();
            EndMissionModel ending = EndMissionModel.From(monitor);
            Assert.That(ending.Label, Is.EqualTo("Ending…"));
            Assert.That(ending.Enabled, Is.False);
        }
    }
}
