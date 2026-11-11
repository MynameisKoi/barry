using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class EmergencyStopGestureTests
    {
        [Test]
        public void BothGripsAndA_HeldForPointThreeSeconds_FiresOnce()
        {
            var gesture = new EmergencyStopGesture();

            Assert.That(gesture.Update(true, true, true, 0.1f), Is.False);
            Assert.That(gesture.Update(true, true, true, 0.1f), Is.False);
            Assert.That(gesture.Update(true, true, true, 0.1f), Is.True);   // 0.3 s reached
            Assert.That(gesture.Update(true, true, true, 0.5f), Is.False);  // never repeats while held
        }

        [TestCase(false, true, true)]
        [TestCase(true, false, true)]
        [TestCase(true, true, false)]
        [TestCase(false, false, true)]
        [TestCase(true, false, false)]
        public void AnyMissingInput_NeverFires(bool leftGrip, bool rightGrip, bool a)
        {
            var gesture = new EmergencyStopGesture();
            for (int i = 0; i < 50; i++)
                Assert.That(gesture.Update(leftGrip, rightGrip, a, 0.1f), Is.False);
        }

        [Test]
        public void ReleasingAnyInputBeforeTheHoldTime_RestartsTheTimer()
        {
            var gesture = new EmergencyStopGesture();
            Assert.That(gesture.Update(true, true, true, 0.2f), Is.False);
            Assert.That(gesture.Update(true, true, false, 0.05f), Is.False);   // A released
            Assert.That(gesture.HeldSeconds, Is.EqualTo(0f));
            Assert.That(gesture.Update(true, true, true, 0.2f), Is.False);     // restarted: only 0.2 s
            Assert.That(gesture.Update(true, true, true, 0.1f), Is.True);
        }

        [Test]
        public void MustBeReleasedBeforeItCanFireAgain()
        {
            var gesture = new EmergencyStopGesture();
            Assert.That(gesture.Update(true, true, true, 0.3f), Is.True);
            Assert.That(gesture.Update(true, true, true, 1f), Is.False);
            gesture.Update(false, true, true, 0.01f);
            Assert.That(gesture.Update(true, true, true, 0.3f), Is.True);
        }

        [Test]
        public void SingleFrameLongerThanTheHoldTime_Fires_ButBadDeltasDoNot()
        {
            var gesture = new EmergencyStopGesture();
            Assert.That(gesture.Update(true, true, true, float.NaN), Is.False);
            Assert.That(gesture.Update(true, true, true, -1f), Is.False);
            Assert.That(gesture.Update(true, true, true, 0.5f), Is.True);
        }
    }
}
