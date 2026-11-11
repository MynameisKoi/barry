using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class TeleopStateMachineTests
    {
        [Test]
        public void TryArm_RequiresCenteredSticksControlLinkAndCamera()
        {
            var state = new TeleopStateMachine();

            Assert.That(state.TryArm(false, true, true, true), Is.False);
            Assert.That(state.TryArm(true, false, true, true), Is.False);
            Assert.That(state.TryArm(true, true, false, true), Is.False);
            Assert.That(state.TryArm(true, true, true, false), Is.False);
            Assert.That(state.State, Is.EqualTo(TeleopState.Disarmed));

            Assert.That(state.TryArm(true, true, true, true), Is.True);
            Assert.That(state.State, Is.EqualTo(TeleopState.Armed));
        }

        [Test]
        public void FreshDeflection_Drives_AndReleaseStops()
        {
            var state = ArmedState();

            state.ApplyCommand(new MotionCommand(0.3f, 0f, 0f));
            Assert.That(state.State, Is.EqualTo(TeleopState.Driving));

            state.ApplyCommand(MotionCommand.Zero);
            Assert.That(state.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(state.LastStopReason, Is.EqualTo(StopReason.SticksCentered));
        }

        [Test]
        public void Stop_CannotBeAcknowledgedUntilSticksAreCentered()
        {
            var state = ArmedState();
            state.Stop(StopReason.Operator);

            Assert.That(state.AcknowledgeStop(false), Is.False);
            Assert.That(state.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(state.AcknowledgeStop(true), Is.True);
            Assert.That(state.State, Is.EqualTo(TeleopState.Disarmed));
        }

        [Test]
        public void StopReason_IsNotOverwrittenByDuplicateStop()
        {
            var state = ArmedState();

            Assert.That(state.Stop(StopReason.ControllerLost), Is.True);
            Assert.That(state.Stop(StopReason.Operator), Is.False);
            Assert.That(state.LastStopReason, Is.EqualTo(StopReason.ControllerLost));
        }

        [Test]
        public void EmergencyStop_EntersStoppedFromAnyState_AndUpgradesAnEarlierReason()
        {
            var driving = ArmedState();
            driving.ApplyCommand(new MotionCommand(0.4f, 0f, 0f));
            Assert.That(driving.Stop(StopReason.EmergencyStop), Is.True);
            Assert.That(driving.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(driving.LastStopReason, Is.EqualTo(StopReason.EmergencyStop));

            var stopped = ArmedState();
            stopped.Stop(StopReason.Operator);
            Assert.That(stopped.Stop(StopReason.EmergencyStop), Is.True);   // upgrades Operator
            Assert.That(stopped.LastStopReason, Is.EqualTo(StopReason.EmergencyStop));
            Assert.That(stopped.Stop(StopReason.LinkUnreliable), Is.False); // nothing downgrades it
            Assert.That(stopped.LastStopReason, Is.EqualTo(StopReason.EmergencyStop));
        }

        [Test]
        public void AfterAnEmergencyStop_MotionNeedsAckWithCentredSticksAndAFreshArm()
        {
            var state = ArmedState();
            state.ApplyCommand(new MotionCommand(0.4f, 0f, 0f));
            state.Stop(StopReason.EmergencyStop);

            state.ApplyCommand(new MotionCommand(0.4f, 0f, 0f));            // sticks still deflected
            Assert.That(state.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(state.AcknowledgeStop(sticksCentered: false), Is.False);

            Assert.That(state.AcknowledgeStop(sticksCentered: true), Is.True);
            Assert.That(state.State, Is.EqualTo(TeleopState.Disarmed));      // never auto-resumes
            Assert.That(state.TryArm(true, true, true, true), Is.True);
            Assert.That(state.State, Is.EqualTo(TeleopState.Armed));
            state.ApplyCommand(MotionCommand.Zero);
            Assert.That(state.State, Is.EqualTo(TeleopState.Armed));         // only a fresh deflection drives
        }

        private static TeleopStateMachine ArmedState()
        {
            var state = new TeleopStateMachine();
            Assert.That(state.TryArm(true, true, true, true), Is.True);
            return state;
        }
    }
}
