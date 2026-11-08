using Barry.Api;
using Barry.Teleop;
using Barry.UI;
using NUnit.Framework;

namespace Barry.Tests.EditMode.UI
{
    public sealed class Issue34DashboardStatusModelsTests
    {
        [Test]
        public void HeaderChips_AlwaysIncludeWordsAndTone()
        {
            var connected = new StatusResponse { connected = true, control_session_active = true };
            HeaderChipsModel model = HeaderChipsModel.Create(connected, true);

            Assert.That(model.Robot.Text, Is.EqualTo("ROBOT CONNECTED"));
            Assert.That(model.Robot.Tone, Is.EqualTo(StatusTone.Ok));
            Assert.That(model.Control.Text, Is.EqualTo("CONTROL: YOU"));
            Assert.That(model.Control.Tone, Is.EqualTo(StatusTone.Accent));
        }

        [Test]
        public void HeaderChips_DistinguishOtherOperatorAndFreeControl()
        {
            var status = new StatusResponse { connected = true, control_session_active = true };
            Assert.That(
                HeaderChipsModel.Create(status, false).Control.Text,
                Is.EqualTo("CONTROL: OTHER OPERATOR"));

            status.control_session_active = false;
            Assert.That(HeaderChipsModel.Create(status, false).Control.Text, Is.EqualTo("CONTROL: FREE"));
        }

        [Test]
        public void StatusPanel_FormatsApiValuesAndMeasuredRtt()
        {
            var status = new StatusResponse { connected = true, control_session_active = true };
            var telemetry = new TelemetryResponse { battery = 82.4f };
            var camera = new CameraStatusResponse { connected = true, actual_fps = 14.75f };

            StatusPanelModel model = StatusPanelModel.Create(
                status, telemetry, camera, MotionCommand.Zero, true, 23.6f);

            Assert.That(model.Robot.Text, Is.EqualTo("Connected"));
            Assert.That(model.Control.Text, Is.EqualTo("You"));
            Assert.That(model.CameraFps.Text, Is.EqualTo("14.8 fps"));
            Assert.That(model.BackendRtt.Text, Is.EqualTo("24 ms"));
            Assert.That(model.Battery.Text, Is.EqualTo("82 %"));
            Assert.That(model.BatteryFill, Is.EqualTo(0.824f).Within(0.0001f));
            Assert.That(StatusPanelModel.Footer, Is.EqualTo(BarryApiClient.AcceptedDisclaimer));
            Assert.That(StatusPanelModel.Footer, Is.EqualTo("Accepted by API · not proof of movement"));
        }

        [Test]
        public void StatusPanel_UsesPlaceholdersForUnavailableMeasurements()
        {
            StatusPanelModel model = StatusPanelModel.Create(
                null, null, null, MotionCommand.Zero, false);

            Assert.That(model.Robot.Text, Is.EqualTo("Disconnected"));
            Assert.That(model.CameraFps.Text, Is.EqualTo("-- fps"));
            Assert.That(model.BackendRtt.Text, Is.EqualTo("-- ms"));
            Assert.That(model.Battery.Text, Is.EqualTo("-- %"));
        }

        [Test]
        public void StatusPanel_TreatsJsonNullBatteryMappedToZeroAsUnknown()
        {
            var telemetry = new TelemetryResponse { battery = 0f };

            StatusPanelModel model = StatusPanelModel.Create(
                null, telemetry, null, MotionCommand.Zero, false);

            Assert.That(model.Battery.Text, Is.EqualTo("-- %"));
            Assert.That(model.Battery.Tone, Is.EqualTo(StatusTone.Neutral));
            Assert.That(model.BatteryFill, Is.EqualTo(0f));
        }

        [Test]
        public void CommandBars_AreCentreZeroAndClampInput()
        {
            var positive = new CommandBarModel("Forward", 0.7f);
            var negative = new CommandBarModel("Turn", -0.3f);
            var clamped = new CommandBarModel("Strafe", 2f);

            Assert.That(positive.FillStart, Is.EqualTo(0.5f));
            Assert.That(positive.FillEnd, Is.EqualTo(0.85f));
            Assert.That(positive.ValueText, Is.EqualTo("0.70"));
            Assert.That(negative.FillStart, Is.EqualTo(0.35f));
            Assert.That(negative.FillEnd, Is.EqualTo(0.5f));
            Assert.That(negative.ValueText, Is.EqualTo("−0.30"));
            Assert.That(clamped.Value, Is.EqualTo(1f));
            Assert.That(clamped.FillEnd, Is.EqualTo(1f));
        }

        [Test]
        public void StatusPanel_MapsMotionCommandToNamedBars()
        {
            var command = new MotionCommand(0.4f, -0.2f, 0.1f);
            StatusPanelModel model = StatusPanelModel.Create(null, null, null, command, false);

            Assert.That(model.Forward.Value, Is.EqualTo(0.4f));
            Assert.That(model.Strafe.Value, Is.EqualTo(-0.2f));
            Assert.That(model.Turn.Value, Is.EqualTo(0.1f));
        }
    }
}
