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

        private static TeleopStateMachine ArmedState()
        {
            var state = new TeleopStateMachine();
            Assert.That(state.TryArm(true, true, true, true), Is.True);
            return state;
        }
    }
}
