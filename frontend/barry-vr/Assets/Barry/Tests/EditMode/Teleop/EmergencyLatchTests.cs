using Barry.Api;
using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class EmergencyLatchTests
    {
        private static TelemetryResponse Telemetry(bool? latched) =>
            latched == null
                ? new TelemetryResponse()
                : new TelemetryResponse { control_state = new ControlState { emergency_stopped = latched.Value } };

        [Test]
        public void StartsClear_AndResetDoesNothingWhenNotLatched()
        {
            var latch = new EmergencyLatch();
            Assert.That(latch.IsLatched, Is.False);
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.None));
            Assert.That(latch.RequestReset(), Is.EqualTo(ResetStep.None));
        }

        [Test]
        public void SendingAnEmergencyStop_LatchesImmediately()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            Assert.That(latch.IsLatched, Is.True);
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.Idle));
        }

        [Test]
        public void PiReportingTheLatch_LatchesAndRaisesExternallyLatchedOnce()
        {
            var latch = new EmergencyLatch();
            int raised = 0;
            latch.ExternallyLatched += () => raised++;

            latch.OnTelemetry(Telemetry(true));
            latch.OnTelemetry(Telemetry(true));

            Assert.That(latch.IsLatched, Is.True);
            Assert.That(raised, Is.EqualTo(1));
        }

        [Test]
        public void OwnEmergencyStop_DoesNotRaiseExternallyLatched()
        {
            var latch = new EmergencyLatch();
            int raised = 0;
            latch.ExternallyLatched += () => raised++;
            latch.OnEmergencyStopRequested();
            latch.OnTelemetry(Telemetry(true));
            Assert.That(raised, Is.EqualTo(0));
        }

        [Test]
        public void ResetNeedsTwoPresses_FirstOnlyAsksForConfirmation()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();

            Assert.That(latch.RequestReset(), Is.EqualTo(ResetStep.AwaitConfirmation));
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.Confirming));
            Assert.That(latch.IsLatched, Is.True);                       // nothing reset yet
            Assert.That(latch.ConfirmSecondsLeft, Is.EqualTo(EmergencyLatch.ConfirmWindowSeconds));

            Assert.That(latch.RequestReset(), Is.EqualTo(ResetStep.SendReset));
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.Resetting));
            Assert.That(latch.RequestReset(), Is.EqualTo(ResetStep.None));   // no double send
        }

        [Test]
        public void UnconfirmedReset_LapsesAfterThreeSeconds_AndNeedsTheFirstPressAgain()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();

            latch.Tick(2.9f);
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.Confirming));
            latch.Tick(0.2f);
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.Idle));

            Assert.That(latch.RequestReset(), Is.EqualTo(ResetStep.AwaitConfirmation));   // not SendReset
        }

        [Test]
        public void SuccessfulReset_ClearsTheLatch_AndNothingElseDoes()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();
            latch.RequestReset();

            latch.OnResetCompleted(true);

            Assert.That(latch.IsLatched, Is.False);
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.None));
        }

        [Test]
        public void FailedReset_StaysLatched_ShowsTheError_AndNeedsConfirmationAgain()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();
            latch.RequestReset();

            latch.OnResetCompleted(false, "Backend unreachable.");

            Assert.That(latch.IsLatched, Is.True);
            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.Idle));
            Assert.That(latch.LastError, Is.EqualTo("Backend unreachable."));
            Assert.That(latch.RequestReset(), Is.EqualTo(ResetStep.AwaitConfirmation));
        }

        [Test]
        public void StaleTelemetryBeforeTheStopLands_DoesNotUnlatch()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();

            latch.OnTelemetry(Telemetry(false));   // sampled before the e-stop reached the Pi

            Assert.That(latch.IsLatched, Is.True);
        }

        [Test]
        public void PiReportingClearAfterDelivery_Unlatches()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.OnEmergencyStopCompleted(true);

            latch.OnTelemetry(Telemetry(false));

            Assert.That(latch.IsLatched, Is.False);
        }

        [Test]
        public void PiReportingClearAfterItConfirmedTheLatch_Unlatches()
        {
            var latch = new EmergencyLatch();
            latch.OnTelemetry(Telemetry(true));
            latch.OnTelemetry(Telemetry(false));
            Assert.That(latch.IsLatched, Is.False);
        }

        [Test]
        public void TelemetryClearDuringAReset_DoesNotRaceTheResetResult()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.OnEmergencyStopCompleted(true);
            latch.RequestReset();
            latch.RequestReset();

            latch.OnTelemetry(Telemetry(false));
            Assert.That(latch.IsLatched, Is.True);          // the reset result decides

            latch.OnResetCompleted(true);
            Assert.That(latch.IsLatched, Is.False);
        }

        [Test]
        public void MissingTelemetry_IsIgnored()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.OnEmergencyStopCompleted(true);
            latch.OnTelemetry(null);
            latch.OnTelemetry(Telemetry(null));
            Assert.That(latch.IsLatched, Is.True);
        }

        [Test]
        public void ANewEmergencyStopDuringConfirmation_CancelsTheConfirmation()
        {
            var latch = new EmergencyLatch();
            latch.OnEmergencyStopRequested();
            latch.RequestReset();

            latch.OnEmergencyStopRequested();

            Assert.That(latch.Phase, Is.EqualTo(ResetPhase.Idle));
            Assert.That(latch.RequestReset(), Is.EqualTo(ResetStep.AwaitConfirmation));
        }
    }
}
