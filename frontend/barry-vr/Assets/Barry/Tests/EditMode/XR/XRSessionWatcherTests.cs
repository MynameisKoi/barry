using System;
using Barry.Teleop;
using Barry.XR;
using NUnit.Framework;

namespace Barry.Tests.EditMode.XR
{
    public sealed class XRSessionWatcherTests
    {
        [Test]
        public void AvailabilityLoss_DoesNotFireBeforeAvailabilityWasObserved()
        {
            var detector = new AvailabilityLossDetector();

            Assert.That(detector.Update(false), Is.False);
            Assert.That(detector.Update(false), Is.False);
        }

        [Test]
        public void AvailabilityLoss_FiresOnTrueToFalseTransitionOnly()
        {
            var detector = new AvailabilityLossDetector();

            Assert.That(detector.Update(true), Is.False);
            Assert.That(detector.Update(true), Is.False);
            Assert.That(detector.Update(false), Is.True);
            Assert.That(detector.Update(false), Is.False);
        }

        [Test]
        public void AvailabilityLoss_CanFireAgainAfterRecovery()
        {
            var detector = new AvailabilityLossDetector();
            detector.Update(true);
            Assert.That(detector.Update(false), Is.True);
            Assert.That(detector.Update(true), Is.False);
            Assert.That(detector.Update(false), Is.True);
        }

        [TestCase(true, StopReason.AppPaused)]
        [TestCase(false, StopReason.None)]
        public void PauseDecision_MapsToStopReason(bool paused, StopReason expected)
        {
            Assert.That(XRSessionStopDecision.ForApplicationPause(paused), Is.EqualTo(expected));
        }

        [TestCase(false, StopReason.FocusLost)]
        [TestCase(true, StopReason.None)]
        public void FocusDecision_MapsToStopReason(bool focused, StopReason expected)
        {
            Assert.That(XRSessionStopDecision.ForApplicationFocus(focused), Is.EqualTo(expected));
        }

        [Test]
        public void AvailabilityDecision_ReturnsConfiguredReason()
        {
            var detector = new AvailabilityLossDetector();
            XRSessionStopDecision.ForAvailabilityLoss(detector, true, StopReason.ControllerLost);

            StopReason reason = XRSessionStopDecision.ForAvailabilityLoss(
                detector, false, StopReason.ControllerLost);

            Assert.That(reason, Is.EqualTo(StopReason.ControllerLost));
        }

        [Test]
        public void AvailabilityDecision_RejectsMissingReason()
        {
            Assert.Throws<ArgumentException>(() =>
                XRSessionStopDecision.ForAvailabilityLoss(
                    new AvailabilityLossDetector(), false, StopReason.None));
        }

        [Test]
        public void ControllerTracking_LeftLossRequestsStopOnce()
        {
            var decisions = new ControllerTrackingStopDecision();

            Assert.That(decisions.UpdateLeft(true), Is.EqualTo(StopReason.None));
            Assert.That(decisions.UpdateLeft(false), Is.EqualTo(StopReason.ControllerLost));
            Assert.That(decisions.UpdateLeft(false), Is.EqualTo(StopReason.None));
        }

        [Test]
        public void ControllerTracking_LeftLossCanFireAgainAfterRecovery()
        {
            var decisions = new ControllerTrackingStopDecision();
            decisions.UpdateLeft(true);
            Assert.That(decisions.UpdateLeft(false), Is.EqualTo(StopReason.ControllerLost));
            Assert.That(decisions.UpdateLeft(true), Is.EqualTo(StopReason.None));
            Assert.That(decisions.UpdateLeft(false), Is.EqualTo(StopReason.ControllerLost));
        }

        [Test]
        public void ControllerTracking_HandsHaveIndependentLossEdges()
        {
            var decisions = new ControllerTrackingStopDecision();
            decisions.UpdateLeft(true);
            decisions.UpdateRight(true);

            Assert.That(decisions.UpdateLeft(false), Is.EqualTo(StopReason.ControllerLost));
            Assert.That(decisions.UpdateRight(false), Is.EqualTo(StopReason.ControllerLost));
        }
    }
}
