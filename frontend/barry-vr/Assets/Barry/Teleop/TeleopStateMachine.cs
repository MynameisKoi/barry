using System;

namespace Barry.Teleop
{
    public enum TeleopState
    {
        Disarmed,
        Armed,
        Driving,
        Stopped
    }

    public enum StopReason
    {
        None,
        SticksCentered,
        ControllerLost,
        AppPaused,
        FocusLost,
        XrSessionEnded,
        ControlDenied,
        ControlExpired,
        LinkUnreliable,
        BackendUnreachable,
        StatusUnavailable,
        CameraUnavailable,
        Operator
    }

    public sealed class TeleopStateMachine
    {
        private bool requiresFreshDeflection;

        public TeleopState State { get; private set; } = TeleopState.Disarmed;
        public StopReason LastStopReason { get; private set; } = StopReason.None;
        public event Action<TeleopState, TeleopState> StateChanged;

        public bool TryArm(bool sticksCentered, bool hasControl, bool linkAvailable, bool cameraAvailable)
        {
            if (State != TeleopState.Disarmed || !sticksCentered || !hasControl || !linkAvailable || !cameraAvailable)
                return false;

            // TryArm itself proves the sticks were centred. The next non-zero
            // sample is therefore the required fresh deflection.
            requiresFreshDeflection = false;
            LastStopReason = StopReason.None;
            TransitionTo(TeleopState.Armed);
            return true;
        }

        public void ApplyCommand(MotionCommand command)
        {
            if (State == TeleopState.Armed)
            {
                if (command.IsCentered)
                {
                    requiresFreshDeflection = false;
                    return;
                }

                if (!requiresFreshDeflection) TransitionTo(TeleopState.Driving);
            }
            else if (State == TeleopState.Driving && command.IsCentered)
            {
                Stop(StopReason.SticksCentered);
            }
        }

        public bool Stop(StopReason reason)
        {
            if (reason == StopReason.None) throw new ArgumentException("A stop reason is required.", nameof(reason));
            if (State == TeleopState.Stopped) return false;
            LastStopReason = reason;
            requiresFreshDeflection = true;
            TransitionTo(TeleopState.Stopped);
            return true;
        }

        public bool AcknowledgeStop(bool sticksCentered)
        {
            if (State != TeleopState.Stopped || !sticksCentered) return false;
            TransitionTo(TeleopState.Disarmed);
            return true;
        }

        public void Disarm()
        {
            requiresFreshDeflection = true;
            TransitionTo(TeleopState.Disarmed);
        }

        private void TransitionTo(TeleopState next)
        {
            if (State == next) return;
            TeleopState previous = State;
            State = next;
            StateChanged?.Invoke(previous, next);
        }
    }
}
