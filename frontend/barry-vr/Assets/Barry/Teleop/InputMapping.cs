using System;
using UnityEngine;

namespace Barry.Teleop
{
    public readonly struct MotionCommand : IEquatable<MotionCommand>
    {
        public static readonly MotionCommand Zero = new MotionCommand(0f, 0f, 0f);

        public readonly float Forward;
        public readonly float Strafe;
        public readonly float Turn;

        public MotionCommand(float forward, float strafe, float turn)
        {
            Forward = forward;
            Strafe = strafe;
            Turn = turn;
        }

        public bool IsCentered => Forward == 0f && Strafe == 0f && Turn == 0f;

        public bool Equals(MotionCommand other) =>
            Forward == other.Forward && Strafe == other.Strafe && Turn == other.Turn;

        public override bool Equals(object obj) => obj is MotionCommand other && Equals(other);
        public override int GetHashCode()
        {
            unchecked
            {
                int hash = Forward.GetHashCode();
                hash = (hash * 397) ^ Strafe.GetHashCode();
                return (hash * 397) ^ Turn.GetHashCode();
            }
        }
        public static bool operator ==(MotionCommand left, MotionCommand right) => left.Equals(right);
        public static bool operator !=(MotionCommand left, MotionCommand right) => !left.Equals(right);
    }

    public sealed class InputMapping
    {
        private float deadZone;

        public float DeadZone
        {
            get => deadZone;
            set => deadZone = Mathf.Clamp(value, 0f, 0.99f);
        }

        public bool InvertForward { get; set; }
        public bool InvertStrafe { get; set; }
        public bool InvertTurn { get; set; }

        public InputMapping(float deadZone = 0.12f)
        {
            DeadZone = deadZone;
        }

        public MotionCommand Map(Vector2 leftStick, Vector2 rightStick)
        {
            Vector2 left = ApplyRadialDeadZone(Sanitize(leftStick), DeadZone);
            Vector2 right = ApplyRadialDeadZone(Sanitize(rightStick), DeadZone);

            float forward = InvertForward ? -left.y : left.y;
            float strafe = InvertStrafe ? -left.x : left.x;
            float turn = InvertTurn ? -right.x : right.x;
            return new MotionCommand(Round(forward), Round(strafe), Round(turn));
        }

        public static Vector2 ApplyRadialDeadZone(Vector2 value, float deadZone)
        {
            deadZone = Mathf.Clamp(deadZone, 0f, 0.99f);
            float magnitude = Mathf.Clamp01(value.magnitude);
            if (magnitude <= deadZone) return Vector2.zero;
            float scaledMagnitude = (magnitude - deadZone) / (1f - deadZone);
            return value.normalized * scaledMagnitude;
        }

        private static Vector2 Sanitize(Vector2 value) => new Vector2(Sanitize(value.x), Sanitize(value.y));

        private static float Sanitize(float value) =>
            float.IsNaN(value) || float.IsInfinity(value) ? 0f : Mathf.Clamp(value, -1f, 1f);

        private static float Round(float value) => Mathf.Round(Mathf.Clamp(value, -1f, 1f) * 100f) / 100f;
    }
}
