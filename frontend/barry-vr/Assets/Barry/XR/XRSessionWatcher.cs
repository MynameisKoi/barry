using System;
using System.Collections.Generic;
using Barry.Teleop;
using UnityEngine;
using UnityEngine.XR;

namespace Barry.XR
{
    /// <summary>
    /// Pure transition helper. A loss is reported only after availability has
    /// first been observed, preventing startup without an XR device from being
    /// mistaken for a runtime disconnect.
    /// </summary>
    public sealed class AvailabilityLossDetector
    {
        private bool hasBeenAvailable;
        private bool wasAvailable;

        public bool Update(bool isAvailable)
        {
            bool lost = hasBeenAvailable && wasAvailable && !isAvailable;
            hasBeenAvailable |= isAvailable;
            wasAvailable = isAvailable;
            return lost;
        }

        public void Reset()
        {
            hasBeenAvailable = false;
            wasAvailable = false;
        }
    }

    public static class XRSessionStopDecision
    {
        public static StopReason ForApplicationPause(bool paused) =>
            paused ? StopReason.AppPaused : StopReason.None;

        public static StopReason ForApplicationFocus(bool hasFocus) =>
            hasFocus ? StopReason.None : StopReason.FocusLost;

        public static StopReason ForAvailabilityLoss(
            AvailabilityLossDetector detector,
            bool isAvailable,
            StopReason lossReason)
        {
            if (detector == null) throw new ArgumentNullException(nameof(detector));
            if (lossReason == StopReason.None) throw new ArgumentException("A loss reason is required.", nameof(lossReason));
            return detector.Update(isAvailable) ? lossReason : StopReason.None;
        }
    }

    public sealed class ControllerTrackingStopDecision
    {
        private readonly AvailabilityLossDetector left = new AvailabilityLossDetector();
        private readonly AvailabilityLossDetector right = new AvailabilityLossDetector();

        public StopReason UpdateLeft(bool isTracked) => Update(left, isTracked);
        public StopReason UpdateRight(bool isTracked) => Update(right, isTracked);

        public void Reset()
        {
            left.Reset();
            right.Reset();
        }

        private static StopReason Update(AvailabilityLossDetector detector, bool isTracked) =>
            XRSessionStopDecision.ForAvailabilityLoss(
                detector, isTracked, StopReason.ControllerLost);
    }

    /// <summary>
    /// Converts Unity application and XR lifecycle changes into best-effort stop
    /// requests. Robot safety remains the backend watchdog's responsibility.
    /// </summary>
    public sealed class XRSessionWatcher : MonoBehaviour
    {
        private readonly AvailabilityLossDetector xrSession = new AvailabilityLossDetector();
        private readonly AvailabilityLossDetector headsetPresence = new AvailabilityLossDetector();
        private readonly ControllerTrackingStopDecision controllerTracking = new ControllerTrackingStopDecision();
        private readonly List<XRDisplaySubsystem> displays = new List<XRDisplaySubsystem>();

        public event Action<StopReason> StopRequested;

        private void OnEnable()
        {
            xrSession.Reset();
            headsetPresence.Reset();
            controllerTracking.Reset();
        }

        private void Update()
        {
            Raise(XRSessionStopDecision.ForAvailabilityLoss(
                xrSession, IsAnyXrDisplayRunning(), StopReason.XrSessionEnded));

            if (TryReadUserPresence(out bool userPresent))
            {
                // StopReason has no HeadsetRemoved value yet; AppPaused is the
                // matching application-lifecycle reason currently used by Teleop.
                Raise(XRSessionStopDecision.ForAvailabilityLoss(
                    headsetPresence, userPresent, StopReason.AppPaused));
            }

            if (TryReadControllerTracking(XRNode.LeftHand, out bool leftTracked))
            {
                Raise(controllerTracking.UpdateLeft(leftTracked));
            }

            if (TryReadControllerTracking(XRNode.RightHand, out bool rightTracked))
            {
                Raise(controllerTracking.UpdateRight(rightTracked));
            }
        }

        private void OnApplicationPause(bool paused)
        {
            Raise(XRSessionStopDecision.ForApplicationPause(paused));
        }

        private void OnApplicationFocus(bool hasFocus)
        {
            Raise(XRSessionStopDecision.ForApplicationFocus(hasFocus));
        }

        private bool IsAnyXrDisplayRunning()
        {
            displays.Clear();
            SubsystemManager.GetSubsystems(displays);
            for (int i = 0; i < displays.Count; i++)
            {
                if (displays[i] != null && displays[i].running) return true;
            }
            return false;
        }

        private static bool TryReadUserPresence(out bool userPresent)
        {
            InputDevice headset = InputDevices.GetDeviceAtXRNode(XRNode.Head);
            if (!headset.isValid)
            {
                userPresent = false;
                return false;
            }
            return headset.TryGetFeatureValue(CommonUsages.userPresence, out userPresent);
        }

        private static bool TryReadControllerTracking(XRNode node, out bool isTracked)
        {
            InputDevice controller = InputDevices.GetDeviceAtXRNode(node);
            if (!controller.isValid)
            {
                isTracked = false;
                return true;
            }
            if (controller.TryGetFeatureValue(CommonUsages.isTracked, out isTracked)) return true;
            if (controller.TryGetFeatureValue(CommonUsages.trackingState, out InputTrackingState state))
            {
                isTracked = (state & (InputTrackingState.Position | InputTrackingState.Rotation)) != 0;
                return true;
            }
            isTracked = false;
            return false;
        }

        private void Raise(StopReason reason)
        {
            if (reason != StopReason.None) StopRequested?.Invoke(reason);
        }
    }
}
