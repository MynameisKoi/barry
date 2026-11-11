using System;

namespace Barry.Teleop
{
    public enum ConfirmStep
    {
        /// <summary>Nothing to do (a request is already in flight).</summary>
        None,
        /// <summary>First press: show "press again to confirm". Nothing is sent.</summary>
        AwaitConfirmation,
        /// <summary>Second press inside the window: do it now.</summary>
        Confirmed
    }

    /// <summary>
    /// The two-press pattern used for destructive actions (end mission; the e-stop reset has the same shape):
    /// the first press only arms, a second press within <see cref="WindowSeconds"/> confirms, and an
    /// unconfirmed press lapses back to idle.
    /// </summary>
    public sealed class ConfirmGate
    {
        public const float DefaultWindowSeconds = 3f;

        public float WindowSeconds { get; }
        public bool Armed { get; private set; }
        public bool Busy { get; private set; }
        public float SecondsLeft { get; private set; }

        public ConfirmGate(float windowSeconds = DefaultWindowSeconds)
        {
            if (windowSeconds <= 0f) throw new ArgumentOutOfRangeException(nameof(windowSeconds));
            WindowSeconds = windowSeconds;
        }

        public ConfirmStep Press()
        {
            if (Busy) return ConfirmStep.None;
            if (!Armed)
            {
                Armed = true;
                SecondsLeft = WindowSeconds;
                return ConfirmStep.AwaitConfirmation;
            }

            Armed = false;
            SecondsLeft = 0f;
            Busy = true;
            return ConfirmStep.Confirmed;
        }

        public void Tick(float unscaledDeltaTime)
        {
            if (!Armed) return;
            if (float.IsNaN(unscaledDeltaTime) || unscaledDeltaTime < 0f) unscaledDeltaTime = 0f;
            SecondsLeft -= unscaledDeltaTime;
            if (SecondsLeft > 0f) return;
            Armed = false;
            SecondsLeft = 0f;
        }

        /// <summary>The confirmed action finished (success or failure): back to idle.</summary>
        public void Complete()
        {
            Busy = false;
            Armed = false;
            SecondsLeft = 0f;
        }

        public void Cancel()
        {
            if (!Busy) Complete();
        }
    }
}
