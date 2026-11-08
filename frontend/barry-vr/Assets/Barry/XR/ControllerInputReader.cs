using System;
using Barry.Teleop;
using UnityEngine;
using UnityEngine.InputSystem;

namespace Barry.XR
{
    public enum MovementIntensity
    {
        Low,
        Medium,
        High
    }

    public readonly struct ControllerButtonDecision
    {
        public readonly bool RequestStop;
        public readonly bool ToggleActions;
        public readonly bool IntensityChanged;
        public readonly bool RequestMovementStyle;
        public readonly MovementIntensity Intensity;
        public bool EmitControllerSample => !RequestStop;

        public ControllerButtonDecision(
            bool requestStop,
            bool toggleActions,
            bool intensityChanged,
            bool requestMovementStyle,
            MovementIntensity intensity)
        {
            RequestStop = requestStop;
            ToggleActions = toggleActions;
            IntensityChanged = intensityChanged;
            RequestMovementStyle = requestMovementStyle;
            Intensity = intensity;
        }
    }

    public sealed class ControllerInputDecisions
    {
        public MovementIntensity Intensity { get; private set; } = MovementIntensity.Medium;

        public void SetIntensity(MovementIntensity intensity)
        {
            if (!Enum.IsDefined(typeof(MovementIntensity), intensity))
                throw new ArgumentOutOfRangeException(nameof(intensity));
            Intensity = intensity;
        }

        public ControllerButtonDecision Evaluate(
            bool stopPressed,
            bool toggleActionsPressed,
            bool cycleIntensityPressed,
            TeleopState teleopState)
        {
            // STOP wins the frame and suppresses every lower-priority button action.
            if (stopPressed)
                return new ControllerButtonDecision(true, false, false, false, Intensity);

            bool intensityChanged = false;
            if (cycleIntensityPressed && teleopState != TeleopState.Driving)
            {
                Intensity = Next(Intensity);
                intensityChanged = true;
            }

            return new ControllerButtonDecision(
                false,
                toggleActionsPressed,
                intensityChanged,
                intensityChanged,
                Intensity);
        }

        public static MovementIntensity Next(MovementIntensity value)
        {
            switch (value)
            {
                case MovementIntensity.Low: return MovementIntensity.Medium;
                case MovementIntensity.Medium: return MovementIntensity.High;
                default: return MovementIntensity.Low;
            }
        }
    }

    /// <summary>
    /// Reads configured Input System actions without owning BarryApp. Scene wiring
    /// binds ControllerInputSampled to BarryApp.SetControllerInput,
    /// OperatorStopRequested to BarryApp.RequestOperatorStop, and
    /// MovementStyleRequested to BarryApiClient.SetMovementStyle. This event seam
    /// avoids the Barry.App -> Barry.XR assembly cycle.
    /// </summary>
    public sealed class ControllerInputReader : MonoBehaviour
    {
        [SerializeField] private InputActionReference leftMove;
        [SerializeField] private InputActionReference rightTurn;
        [SerializeField] private InputActionReference stopButton;
        [SerializeField] private InputActionReference toggleActions;
        [SerializeField] private InputActionReference cycleIntensity;

        private readonly ControllerInputDecisions decisions = new ControllerInputDecisions();

        public Func<TeleopState> TeleopStateProvider { get; set; }
        public MovementIntensity Intensity => decisions.Intensity;

        public event Action<Vector2, Vector2> ControllerInputSampled;
        public event Action OperatorStopRequested;
        public event Action ToggleActionsRequested;
        public event Action<MovementIntensity> IntensityChanged;
        public event Action<MovementIntensity> MovementStyleRequested;

        public void SetIntensity(MovementIntensity intensity)
        {
            decisions.SetIntensity(intensity);
        }

        private void OnEnable()
        {
            SetActionsEnabled(true);
        }

        private void OnDisable()
        {
            SetActionsEnabled(false);
        }

        private void Update()
        {
            TeleopState teleopState = TeleopStateProvider?.Invoke() ?? TeleopState.Disarmed;
            ControllerButtonDecision decision = decisions.Evaluate(
                WasPressed(stopButton),
                WasPressed(toggleActions),
                WasPressed(cycleIntensity),
                teleopState);

            if (!decision.EmitControllerSample)
            {
                OperatorStopRequested?.Invoke();
                return;
            }

            Vector2 left = ReadVector2(leftMove);
            float turn = ReadVector2(rightTurn).x;
            ControllerInputSampled?.Invoke(left, new Vector2(turn, 0f));

            if (decision.ToggleActions) ToggleActionsRequested?.Invoke();
            if (decision.IntensityChanged) IntensityChanged?.Invoke(decision.Intensity);
            if (decision.RequestMovementStyle) MovementStyleRequested?.Invoke(decision.Intensity);
        }

        private void SetActionsEnabled(bool enabled)
        {
            SetEnabled(leftMove, enabled);
            SetEnabled(rightTurn, enabled);
            SetEnabled(stopButton, enabled);
            SetEnabled(toggleActions, enabled);
            SetEnabled(cycleIntensity, enabled);
        }

        private static Vector2 ReadVector2(InputActionReference reference) =>
            reference?.action?.ReadValue<Vector2>() ?? Vector2.zero;

        private static bool WasPressed(InputActionReference reference) =>
            reference?.action?.WasPressedThisFrame() ?? false;

        private static void SetEnabled(InputActionReference reference, bool enabled)
        {
            InputAction action = reference?.action;
            if (action == null) return;
            if (enabled) action.Enable();
            else action.Disable();
        }
    }
}
