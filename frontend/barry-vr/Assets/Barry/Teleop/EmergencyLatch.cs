using System;
using Barry.Api;

namespace Barry.Teleop
{
    public enum ResetPhase
    {
        /// <summary>Not latched: nothing to reset.</summary>
        None,
        /// <summary>Latched; the operator has not started a reset.</summary>
        Idle,
        /// <summary>First press done; a second press within <see cref="EmergencyLatch.ConfirmWindowSeconds"/> confirms.</summary>
        Confirming,
        /// <summary>The reset request is in flight.</summary>
        Resetting
    }

    public enum ResetStep
    {
        None,
        /// <summary>First press: show "press again to confirm". Nothing is sent.</summary>
        AwaitConfirmation,
        /// <summary>Confirmed: the caller sends ResetEmergency() now.</summary>
        SendReset
    }

    /// <summary>
    /// Tracks the e-stop latch and the confirmed reset. The Pi reports the latch in
    /// <c>/api/v1/telemetry</c> → <c>control_state.emergency_stopped</c> (<c>/status</c> does not carry it).
    /// The latch is also set locally the moment an e-stop is sent, so the HUD never lags the button.
    /// It is cleared only by a successful reset, or by the Pi reporting "not latched" in a sample taken
    /// after the e-stop was delivered. It never auto-resets and never resumes motion (that stays with the
    /// teleop state machine: sticks centred, then a fresh deflection).
    /// </summary>
    public sealed class EmergencyLatch
    {
        public const float ConfirmWindowSeconds = 3f;

        private bool latched;
        private bool piConfirmedLatch;
        private bool stopDelivered;

        public bool IsLatched => latched;
        public ResetPhase Phase { get; private set; } = ResetPhase.None;
        public float ConfirmSecondsLeft { get; private set; }
        public string LastError { get; private set; }

        /// <summary>Raised when the Pi reports a latch this client did not cause (e.g. another operator).</summary>
        public event Action ExternallyLatched;

        /// <summary>The operator fired the e-stop. Latch immediately.</summary>
        public void OnEmergencyStopRequested()
        {
            latched = true;
            piConfirmedLatch = false;
            stopDelivered = false;
            LastError = null;
            if (Phase == ResetPhase.Confirming || Phase == ResetPhase.None) Phase = ResetPhase.Idle;
            ConfirmSecondsLeft = 0f;
        }

        public void OnEmergencyStopCompleted(bool delivered)
        {
            stopDelivered = delivered;
        }

        /// <summary>Feed every telemetry sample (null = unknown, ignored).</summary>
        public void OnTelemetry(TelemetryResponse telemetry)
        {
            bool? reported = telemetry?.control_state?.emergency_stopped;
            if (reported == null) return;

            if (reported.Value)
            {
                piConfirmedLatch = true;
                if (!latched) // another source latched it (e.g. the Pi or another client)
                {
                    latched = true;
                    Phase = ResetPhase.Idle;
                    ExternallyLatched?.Invoke();
                }
                return;
            }

            // Pi says "not latched". Trust it only if we are not waiting for our own e-stop to land.
            if (!latched || Phase == ResetPhase.Resetting) return;
            if (piConfirmedLatch || stopDelivered) Clear();
        }

        public ResetStep RequestReset()
        {
            if (!latched) return ResetStep.None;
            switch (Phase)
            {
                case ResetPhase.Idle:
                    Phase = ResetPhase.Confirming;
                    ConfirmSecondsLeft = ConfirmWindowSeconds;
                    LastError = null;
                    return ResetStep.AwaitConfirmation;
                case ResetPhase.Confirming:
                    Phase = ResetPhase.Resetting;
                    ConfirmSecondsLeft = 0f;
                    return ResetStep.SendReset;
                default:
                    return ResetStep.None;
            }
        }

        /// <summary>Lets an unconfirmed reset lapse back to Idle.</summary>
        public void Tick(float unscaledDeltaTime)
        {
            if (Phase != ResetPhase.Confirming) return;
            if (float.IsNaN(unscaledDeltaTime) || unscaledDeltaTime < 0f) unscaledDeltaTime = 0f;
            ConfirmSecondsLeft -= unscaledDeltaTime;
            if (ConfirmSecondsLeft > 0f) return;
            ConfirmSecondsLeft = 0f;
            Phase = ResetPhase.Idle;
        }

        public void OnResetCompleted(bool success, string error = null)
        {
            if (Phase != ResetPhase.Resetting) return;
            if (success)
            {
                Clear();
                return;
            }
            Phase = ResetPhase.Idle;
            LastError = string.IsNullOrWhiteSpace(error) ? "Reset failed." : error;
        }

        private void Clear()
        {
            latched = false;
            piConfirmedLatch = false;
            stopDelivered = false;
            Phase = ResetPhase.None;
            ConfirmSecondsLeft = 0f;
            LastError = null;
        }
    }
}
