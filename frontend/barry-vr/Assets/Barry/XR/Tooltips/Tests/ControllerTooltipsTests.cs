using System;
using System.Linq;
using NUnit.Framework;
using UnityEngine;

namespace Barry.XR.Tooltips.Tests
{
    public sealed class ControllerTooltipsTests
    {
        [Test]
        public void Layout_ContainsExactControllerCopy()
        {
            Assert.That(ControllerTooltipLayout.Definitions.Select(item => item.PlainText), Is.EqualTo(new[]
            {
                "L Move · strafe",
                "X Intensity",
                "R Turn",
                "A STOP",
                "B Actions"
            }));
        }

        [Test]
        public void Layout_AssignsHintsToCorrectHands()
        {
            Assert.That(
                ControllerTooltipLayout.Definitions.Count(item => item.Hand == ControllerHand.Left),
                Is.EqualTo(2));
            Assert.That(
                ControllerTooltipLayout.Definitions.Count(item => item.Hand == ControllerHand.Right),
                Is.EqualTo(3));
            Assert.That(
                ControllerTooltipLayout.Definitions.Single(item => item.IsStop).PlainText,
                Is.EqualTo("A STOP"));
        }

        [Test]
        public void Layout_MatchesMockupPillSizesAndRadiusConvention()
        {
            Assert.That(Find("left_move").SizeMm, Is.EqualTo(new Vector2(114f, 28f)));
            Assert.That(Find("left_intensity").SizeMm, Is.EqualTo(new Vector2(92f, 28f)));
            Assert.That(Find("right_turn").SizeMm, Is.EqualTo(new Vector2(100f, 28f)));
            Assert.That(Find("right_stop").SizeMm, Is.EqualTo(new Vector2(92f, 28f)));
            Assert.That(Find("right_actions").SizeMm, Is.EqualTo(new Vector2(92f, 28f)));
            Assert.That(ControllerTooltipLayout.CanvasScale, Is.EqualTo(0.001f));
        }

        [Test]
        public void RichText_BoldsOnlyKeyPrefixForNormalHints()
        {
            Assert.That(Find("left_move").RichText, Is.EqualTo("<b>L</b> Move · strafe"));
            Assert.That(Find("right_turn").RichText, Is.EqualTo("<b>R</b> Turn"));
        }

        [Test]
        public void Create_ParentsWorldSpaceRootsAndSupportsVisibilityOption()
        {
            var left = new GameObject("Left Controller");
            var right = new GameObject("Right Controller");
            ControllerTooltips tooltips = null;
            try
            {
                tooltips = ControllerTooltips.Create(left.transform, right.transform, null);

                Assert.That(tooltips.LeftTooltipRoot.parent, Is.SameAs(left.transform));
                Assert.That(tooltips.RightTooltipRoot.parent, Is.SameAs(right.transform));
                Assert.That(tooltips.LeftTooltipRoot.localScale, Is.EqualTo(Vector3.one * 0.001f));
                Assert.That(tooltips.TooltipsVisible, Is.True);

                tooltips.SetVisible(false);
                Assert.That(tooltips.TooltipsVisible, Is.False);
                Assert.That(tooltips.LeftTooltipRoot.gameObject.activeSelf, Is.False);
                Assert.That(tooltips.RightTooltipRoot.gameObject.activeSelf, Is.False);
            }
            finally
            {
                if (tooltips != null) UnityEngine.Object.DestroyImmediate(tooltips.gameObject);
                UnityEngine.Object.DestroyImmediate(left);
                UnityEngine.Object.DestroyImmediate(right);
            }
        }

        [Test]
        public void Create_RejectsMissingControllerTransforms()
        {
            var controller = new GameObject("Controller");
            try
            {
                Assert.Throws<ArgumentNullException>(() =>
                    ControllerTooltips.Create(null, controller.transform, null));
                Assert.Throws<ArgumentNullException>(() =>
                    ControllerTooltips.Create(controller.transform, null, null));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(controller);
            }
        }

        private static ControllerTooltipDefinition Find(string id) =>
            ControllerTooltipLayout.Definitions.Single(item => item.Id == id);
    }
}
