using NUnit.Framework;
using UnityEngine;

namespace Barry.Teleop.Tests
{
    public sealed class InputMappingTests
    {
        [Test]
        public void Map_InsideDeadZone_ReturnsZero()
        {
            var mapping = new InputMapping(0.12f);

            MotionCommand result = mapping.Map(new Vector2(0.05f, 0.05f), new Vector2(0.1f, 0f));

            Assert.That(result, Is.EqualTo(MotionCommand.Zero));
        }

        [Test]
        public void Map_RescalesAfterRadialDeadZone_AndRoundsToTwoDecimals()
        {
            var mapping = new InputMapping(0.2f);

            MotionCommand result = mapping.Map(new Vector2(0f, 0.6f), new Vector2(0.36f, 0f));

            Assert.That(result.Forward, Is.EqualTo(0.5f));
            Assert.That(result.Strafe, Is.Zero);
            Assert.That(result.Turn, Is.EqualTo(0.2f));
        }

        [Test]
        public void Map_AppliesEachAxisInversion()
        {
            var mapping = new InputMapping(0f)
            {
                InvertForward = true,
                InvertStrafe = true,
                InvertTurn = true
            };

            MotionCommand result = mapping.Map(new Vector2(0.25f, -0.5f), new Vector2(0.75f, 0f));

            Assert.That(result.Forward, Is.EqualTo(0.5f));
            Assert.That(result.Strafe, Is.EqualTo(-0.25f));
            Assert.That(result.Turn, Is.EqualTo(-0.75f));
        }

        [Test]
        public void Map_ClampsAndSanitizesInvalidInput()
        {
            var mapping = new InputMapping(0f);

            MotionCommand result = mapping.Map(
                new Vector2(float.NaN, float.PositiveInfinity),
                new Vector2(2f, 0f));

            Assert.That(result.Forward, Is.Zero);
            Assert.That(result.Strafe, Is.Zero);
            Assert.That(result.Turn, Is.EqualTo(1f));
        }
    }
}
