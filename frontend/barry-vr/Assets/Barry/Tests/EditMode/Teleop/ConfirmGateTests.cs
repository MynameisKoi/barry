using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class ConfirmGateTests
    {
        [Test]
        public void FirstPressOnlyArms_SecondWithinTheWindowConfirms()
        {
            var gate = new ConfirmGate();
            Assert.That(gate.Press(), Is.EqualTo(ConfirmStep.AwaitConfirmation));
            Assert.That(gate.Armed, Is.True);
            Assert.That(gate.SecondsLeft, Is.EqualTo(3f));
            Assert.That(gate.Press(), Is.EqualTo(ConfirmStep.Confirmed));
            Assert.That(gate.Busy, Is.True);
        }

        [Test]
        public void AnUnconfirmedPress_LapsesAfterTheWindow_AndNeedsArmingAgain()
        {
            var gate = new ConfirmGate();
            gate.Press();
            gate.Tick(2.9f);
            Assert.That(gate.Armed, Is.True);
            gate.Tick(0.2f);
            Assert.That(gate.Armed, Is.False);
            Assert.That(gate.Press(), Is.EqualTo(ConfirmStep.AwaitConfirmation));
        }

        [Test]
        public void WhileBusy_FurtherPressesDoNothing_UntilComplete()
        {
            var gate = new ConfirmGate();
            gate.Press();
            gate.Press();
            Assert.That(gate.Press(), Is.EqualTo(ConfirmStep.None));
            gate.Complete();
            Assert.That(gate.Busy, Is.False);
            Assert.That(gate.Press(), Is.EqualTo(ConfirmStep.AwaitConfirmation));
        }

        [Test]
        public void BadDeltaTimeIsIgnored()
        {
            var gate = new ConfirmGate();
            gate.Press();
            gate.Tick(float.NaN);
            gate.Tick(-5f);
            Assert.That(gate.Armed, Is.True);
            Assert.That(gate.SecondsLeft, Is.EqualTo(3f));
        }
    }
}
