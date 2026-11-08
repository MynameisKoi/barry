using Barry.Teleop;
using Barry.XR;
using NUnit.Framework;

namespace Barry.Tests.EditMode.XR
{
    public sealed class Issue38ControllerInputDecisionsTests
    {
        [Test]
        public void Stop_HasPriorityAndSuppressesOtherButtonDecisions()
        {
            var helper = new ControllerInputDecisions();

            ControllerButtonDecision decision = helper.Evaluate(
                true, true, true, TeleopState.Armed);

            Assert.That(decision.RequestStop, Is.True);
            Assert.That(decision.ToggleActions, Is.False);
            Assert.That(decision.IntensityChanged, Is.False);
            Assert.That(decision.RequestMovementStyle, Is.False);
            Assert.That(decision.EmitControllerSample, Is.False);
            Assert.That(helper.Intensity, Is.EqualTo(MovementIntensity.Medium));
        }

        [Test]
        public void Intensity_CyclesMediumHighLowMedium()
        {
            var helper = new ControllerInputDecisions();

            Assert.That(Cycle(helper, TeleopState.Armed).Intensity, Is.EqualTo(MovementIntensity.High));
            Assert.That(Cycle(helper, TeleopState.Armed).Intensity, Is.EqualTo(MovementIntensity.Low));
            Assert.That(Cycle(helper, TeleopState.Armed).Intensity, Is.EqualTo(MovementIntensity.Medium));
        }

        [Test]
        public void Intensity_DrivingPressIsIgnoredWithoutLocalOrApiChange()
        {
            var helper = new ControllerInputDecisions();

            ControllerButtonDecision driving = Cycle(helper, TeleopState.Driving);

            Assert.That(driving.IntensityChanged, Is.False);
            Assert.That(driving.RequestMovementStyle, Is.False);
            Assert.That(helper.Intensity, Is.EqualTo(MovementIntensity.Medium));

            ControllerButtonDecision stopped = Cycle(helper, TeleopState.Stopped);
            Assert.That(stopped.IntensityChanged, Is.True);
            Assert.That(stopped.RequestMovementStyle, Is.True);
        }

        [Test]
        public void SetIntensity_SynchronizesOwnerStateBeforeNextCycle()
        {
            var helper = new ControllerInputDecisions();
            helper.SetIntensity(MovementIntensity.High);

            ControllerButtonDecision cycled = Cycle(helper, TeleopState.Armed);

            Assert.That(cycled.Intensity, Is.EqualTo(MovementIntensity.Low));
            Assert.That(cycled.RequestMovementStyle, Is.True);
        }

        [Test]
        public void SetIntensity_RejectsUndefinedValue()
        {
            var helper = new ControllerInputDecisions();
            Assert.Throws<System.ArgumentOutOfRangeException>(() =>
                helper.SetIntensity((MovementIntensity)99));
        }

        [Test]
        public void ToggleActions_IsEdgeDecisionIndependentOfTeleopState()
        {
            var helper = new ControllerInputDecisions();

            ControllerButtonDecision decision = helper.Evaluate(
                false, true, false, TeleopState.Driving);

            Assert.That(decision.ToggleActions, Is.True);
            Assert.That(decision.RequestStop, Is.False);
        }

        [Test]
        public void NoButtons_ProducesNoDiscreteActions()
        {
            var helper = new ControllerInputDecisions();

            ControllerButtonDecision decision = helper.Evaluate(
                false, false, false, TeleopState.Armed);

            Assert.That(decision.RequestStop, Is.False);
            Assert.That(decision.ToggleActions, Is.False);
            Assert.That(decision.IntensityChanged, Is.False);
            Assert.That(decision.RequestMovementStyle, Is.False);
        }

        private static ControllerButtonDecision Cycle(
            ControllerInputDecisions helper,
            TeleopState state) =>
            helper.Evaluate(false, false, true, state);
    }
}
