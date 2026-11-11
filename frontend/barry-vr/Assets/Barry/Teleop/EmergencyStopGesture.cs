using System;

namespace Barry.Teleop
{
    /// <summary>
    /// The dedicated emergency-stop input: BOTH grips plus A held together for <see cref="HoldSeconds"/>.
    /// Separate from the normal STOP (A alone), so it cannot be fired by accident. It fires once per
    /// hold and must be released (any of the three) before it can fire again. Provisional per HANDOFF §6:
    /// verify the combination on the headset.
    /// </summary>
    public sealed class EmergencyStopGesture
    {
        public const float HoldSeconds = 0.3f;

        private float heldFor;
        private bool fired;

        public float HeldSeconds => heldFor;

        /// <summary>Returns true exactly once, on the frame the full combination has been held long enough.</summary>
        public bool Update(bool leftGrip, bool rightGrip, bool buttonA, float unscaledDeltaTime)
        {
            if (float.IsNaN(unscaledDeltaTime) || unscaledDeltaTime < 0f) unscaledDeltaTime = 0f;
            if (!(leftGrip && rightGrip && buttonA))
            {
                heldFor = 0f;
                fired = false;
                return false;
            }

            heldFor += unscaledDeltaTime;
            if (fired || heldFor < HoldSeconds) return false;
            fired = true;
            return true;
        }

        public void Reset()
        {
            heldFor = 0f;
            fired = false;
        }
    }
}
