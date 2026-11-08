using System;
using Barry.Teleop;
using NUnit.Framework;

namespace Barry.UI.Tests
{
    public sealed class StopOverlayModelMappingTests
    {
        [Test]
        public void ControllerLost_UsesExactCopyAndDisabledWaitingAction()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.ControllerLost, true);

            Assert.That(model.Chip, Is.EqualTo("STOPPED"));
            Assert.That(model.Tone, Is.EqualTo(StopOverlayTone.Danger));
            Assert.That(model.Title, Is.EqualTo("Right controller lost"));
            Assert.That(model.Detail, Is.EqualTo("The right controller stopped tracking, so Barry was stopped."));
            Assert.That(model.StopDelivered, Is.True);
            Assert.That(model.StopLine, Is.EqualTo("STOP sent · accepted by the backend"));
            Assert.That(model.PrimaryActionId, Is.EqualTo("wait_for_controller"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Waiting for controller…"));
            Assert.That(model.PrimaryActionEnabled, Is.False);
            AssertSteps(model,
                "Bring the controller back into view, or wake it.",
                "Centre both sticks.",
                "Push a stick to drive again.");
        }

        [Test]
        public void LinkUnreliable_UsesExactCopy()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.LinkUnreliable, true);

            Assert.That(model.Title, Is.EqualTo("Unreliable link"));
            Assert.That(model.Detail,
                Is.EqualTo("Three movement commands timed out (over 300 ms), so Barry was stopped."));
            Assert.That(model.PrimaryActionId, Is.EqualTo("retry_link"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Retry now"));
            Assert.That(model.PrimaryActionEnabled, Is.True);
            AssertSteps(model,
                "Check Wi-Fi between the laptop and Barry.",
                "Wait for the link to turn green.",
                "Centre both sticks, then push to drive.");
        }

        [Test]
        public void BackendUnreachable_UsesUndeliveredCopyWhenStopFailed()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.BackendUnreachable, false);

            Assert.That(model.Title, Is.EqualTo("Backend unreachable"));
            Assert.That(model.Detail, Is.EqualTo("The headset can’t reach the laptop backend."));
            Assert.That(model.StopDelivered, Is.False);
            Assert.That(model.StopLine, Is.EqualTo(
                "STOP couldn’t be delivered · Barry’s on-board watchdog halts it when commands stop"));
            Assert.That(model.PrimaryActionId, Is.EqualTo("retry_connection"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Retry connection"));
            AssertSteps(model,
                "Check the backend is running on the laptop.",
                "Check the headset and laptop are on the same network.",
                "Take control again after reconnecting.");
        }

        [Test]
        public void ControlExpired_UsesExactCopy()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.ControlExpired, true);

            Assert.That(model.Title, Is.EqualTo("Control expired"));
            Assert.That(model.Detail, Is.EqualTo("Your control session ended, so Barry was stopped."));
            Assert.That(model.PrimaryActionId, Is.EqualTo("take_control_again"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Take control again"));
            AssertSteps(model,
                "Take control again.",
                "Centre both sticks, then push to drive.");
        }

        [Test]
        public void CameraUnavailable_UsesWarningToneAndPausedChip()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.CameraUnavailable, true);

            Assert.That(model.Chip, Is.EqualTo("DRIVING PAUSED"));
            Assert.That(model.Tone, Is.EqualTo(StopOverlayTone.Warning));
            Assert.That(model.Title, Is.EqualTo("Camera unavailable"));
            Assert.That(model.Detail, Is.EqualTo(
                "The camera feed stopped. Driving is paused because you can’t see where Barry is going."));
            Assert.That(model.PrimaryActionId, Is.EqualTo("retry_camera"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Retry camera"));
            AssertSteps(model,
                "Wait for the feed to return (retrying).",
                "Centre both sticks, then push to drive.");
        }

        [Test]
        public void OperatorStop_UsesExactCopy()
        {
            StopOverlayModel model = StopOverlayModel.From(StopReason.Operator, true);

            Assert.That(model.Title, Is.EqualTo("Stopped by you"));
            Assert.That(model.Detail, Is.EqualTo("You pressed STOP. Barry is holding still."));
            Assert.That(model.PrimaryActionId, Is.EqualTo("dismiss"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Dismiss"));
            AssertSteps(model,
                "Centre both sticks.",
                "Push a stick to drive again.");
        }

        [TestCase(StopReason.StatusUnavailable, "Backend unreachable")]
        [TestCase(StopReason.ControlDenied, "Control expired")]
        public void RelatedInternalReasons_UseCanonicalOverlay(StopReason reason, string title)
        {
            Assert.That(StopOverlayModel.From(reason, true).Title, Is.EqualTo(title));
        }

        [TestCase(StopReason.AppPaused)]
        [TestCase(StopReason.FocusLost)]
        [TestCase(StopReason.XrSessionEnded)]
        public void LifecycleInterruption_UsesHonestGenericCopy(StopReason reason)
        {
            Assert.That(StopOverlayModel.TryFrom(reason, true, out StopOverlayModel model), Is.True);
            Assert.That(model.Chip, Is.EqualTo("STOPPED"));
            Assert.That(model.Tone, Is.EqualTo(StopOverlayTone.Danger));
            Assert.That(model.Title, Is.EqualTo("Stopped"));
            Assert.That(model.Detail,
                Is.EqualTo("The headset app was paused, lost focus, or its XR session ended, so Barry was stopped."));
            Assert.That(model.PrimaryActionId, Is.EqualTo("dismiss"));
            Assert.That(model.PrimaryActionLabel, Is.EqualTo("Dismiss"));
            AssertSteps(model,
                "Centre both sticks.",
                "Push a stick to drive again.");
        }

        [Test]
        public void SticksCentered_IsNormalReleaseAndProducesNoOverlay()
        {
            Assert.That(
                StopOverlayModel.TryFrom(StopReason.SticksCentered, true, out StopOverlayModel model),
                Is.False);
            Assert.That(model, Is.Null);
            Assert.Throws<ArgumentOutOfRangeException>(() =>
                StopOverlayModel.From(StopReason.SticksCentered, true));
        }

        [Test]
        public void NoneReason_IsRejected()
        {
            Assert.That(StopOverlayModel.TryFrom(StopReason.None, true, out StopOverlayModel model), Is.False);
            Assert.That(model, Is.Null);
            Assert.Throws<ArgumentOutOfRangeException>(() => StopOverlayModel.From(StopReason.None, true));
        }

        private static void AssertSteps(StopOverlayModel model, params string[] expected)
        {
            Assert.That(model.Steps, Has.Count.EqualTo(expected.Length));
            for (int index = 0; index < expected.Length; index++)
            {
                Assert.That(model.Steps[index].Number, Is.EqualTo(index + 1));
                Assert.That(model.Steps[index].Text, Is.EqualTo(expected[index]));
            }
        }
    }
}
