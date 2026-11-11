using Barry.Api;
using Barry.Teleop;
using NUnit.Framework;

namespace Barry.UI.Tests
{
    public sealed class EmergencyHudModelTests
    {
        [Test]
        public void NotLatched_HidesTheHud()
        {
            EmergencyHudModel model = EmergencyHudModel.From(new EmergencyLatch());
            Assert.That(model.Visible, Is.False);
            Assert.That(model.ButtonEnabled, Is.False);
            Assert.That(EmergencyHudModel.From(null).Visible, Is.False);
        }

        [Test]
        public void Latched_ShowsTheWordAndTheDangerTone()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();

            EmergencyHudModel model = EmergencyHudModel.From(latch);

            Assert.That(model.Visible, Is.True);
            Assert.That(model.Chip, Is.EqualTo("E-STOP LATCHED"));   // the word, never colour alone
            Assert.That(model.Tone, Is.EqualTo(StatusTone.Danger));
            Assert.That(model.ButtonLabel, Is.EqualTo("Reset e-stop"));
            Assert.That(model.ButtonEnabled, Is.True);
            Assert.That(model.FooterText, Does.Contain("centre both sticks"));
        }

        [Test]
        public void TheLatchReportedByThePi_ShowsTheSameHud()
        {
            var latch = new EmergencyLatch();
            latch.OnTelemetry(new TelemetryResponse { control_state = new ControlState { emergency_stopped = true } });
            Assert.That(EmergencyHudModel.From(latch).Chip, Is.EqualTo("E-STOP LATCHED"));
        }

        [Test]
        public void Confirming_AsksForASecondPressWithTheCountdown()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();

            EmergencyHudModel model = EmergencyHudModel.From(latch);
            Assert.That(model.ButtonLabel, Is.EqualTo("Press again to confirm (3 s)"));
            Assert.That(model.ButtonEnabled, Is.True);

            latch.Tick(1.2f);
            Assert.That(EmergencyHudModel.From(latch).ButtonLabel, Is.EqualTo("Press again to confirm (2 s)"));
        }

        [Test]
        public void Resetting_DisablesTheButton()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();
            latch.RequestReset();

            EmergencyHudModel model = EmergencyHudModel.From(latch);
            Assert.That(model.ButtonLabel, Is.EqualTo("Resetting…"));
            Assert.That(model.ButtonEnabled, Is.False);
        }

        [Test]
        public void AFailedReset_ShowsTheErrorAndKeepsTheHud()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();
            latch.RequestReset();
            latch.OnResetCompleted(false, "Backend unreachable.");

            EmergencyHudModel model = EmergencyHudModel.From(latch);
            Assert.That(model.Visible, Is.True);
            Assert.That(model.DetailText, Does.StartWith("Backend unreachable."));
        }

        [Test]
        public void AfterAConfirmedReset_TheHudDisappears()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();
            latch.RequestReset();
            latch.OnResetCompleted(true);
            Assert.That(EmergencyHudModel.From(latch).Visible, Is.False);
        }

        [Test]
        public void EmergencyStopReason_HasItsOwnStopOverlay()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.EmergencyStop, true);

            Assert.That(model.Chip, Is.EqualTo("STOPPED"));
            Assert.That(model.Title, Is.EqualTo("Emergency stop"));
            Assert.That(model.Tone, Is.EqualTo(StopOverlayTone.Danger));
            Assert.That(model.PrimaryActionId, Is.EqualTo("reset_estop"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Reset e-stop"));
            Assert.That(StopOverlayModel.TryFrom(StopReason.EmergencyStop, false, out _), Is.True);
        }
    }
}
