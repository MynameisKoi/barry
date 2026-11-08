using Barry.Theme;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.UI.Styling.Tests
{
    public sealed class RoundedPanelTests
    {
        [Test]
        public void SharedMaterial_IsResourcesBackedAndReferencesRoundedPanelShader()
        {
            Material material = Resources.Load<Material>("BarryRoundedPanel");

            Assert.That(material, Is.Not.Null);
            Assert.That(material.shader, Is.Not.Null);
            Assert.That(material.shader.name, Is.EqualTo("Barry/UI/RoundedPanel"));
        }

        [Test]
        public void Parameters_ClampInvalidValues_AndExpandForVisibleEffect()
        {
            var parameters = new RoundedPanelParameters(
                -1f, -2f, 32f, 2f, false,
                Color.white, Color.red, Color.blue);

            Assert.That(parameters.Radius, Is.Zero);
            Assert.That(parameters.BorderWidth, Is.Zero);
            Assert.That(parameters.EffectStrength, Is.EqualTo(1f));
            Assert.That(parameters.ExpandedRect(new Rect(-50f, -25f, 100f, 50f)),
                Is.EqualTo(new Rect(-82f, -57f, 164f, 114f)));
        }

        [Test]
        public void Parameters_DoNotExpandWhenEffectIsDisabled()
        {
            var parameters = new RoundedPanelParameters(
                16f, 1f, 32f, 0f, false,
                Color.white, Color.red, Color.blue);
            Rect rect = new Rect(-50f, -25f, 100f, 50f);

            Assert.That(parameters.ExpandedRect(rect), Is.EqualTo(rect));
        }

        [Test]
        public void EncodeVertex_ExpandsCornerAndStoresSdfData()
        {
            Rect rect = new Rect(-50f, -25f, 100f, 50f);
            Color fill = new Color(1f, 1f, 1f, 0.94f);
            Color border = new Color(0.7f, 0.6f, 1f, 0.8f);
            Color effect = new Color(0.4f, 0.2f, 1f, 1f);
            var parameters = new RoundedPanelParameters(
                16f, 1f, 32f, 0.35f, true, fill, border, effect);
            UIVertex vertex = UIVertex.simpleVert;
            vertex.position = new Vector3(rect.xMin, rect.yMin, 0f);

            RoundedPanelMeshUtility.EncodeVertex(ref vertex, rect, parameters);

            Assert.That(vertex.position, Is.EqualTo(new Vector3(-82f, -57f, 0f)));
            Assert.That(vertex.uv1, Is.EqualTo(new Vector4(-82f, -57f, 0f, 0f)));
            Assert.That(vertex.uv2, Is.EqualTo(new Vector4(50f, 25f, 16f, 1f)));
            Assert.That(vertex.uv3, Is.EqualTo(new Vector4(32f, 0.35f, 0.8f, 1f)));
            Assert.That(vertex.color, Is.EqualTo((Color32)fill));
            Assert.That(vertex.normal, Is.EqualTo(new Vector3(border.r, border.g, border.b)));
            Assert.That(vertex.tangent, Is.EqualTo((Vector4)effect));
        }

        [Test]
        public void EncodeVertex_ClampsRadiusToHalfOfShortestSide()
        {
            Rect rect = new Rect(-10f, -5f, 20f, 10f);
            var parameters = new RoundedPanelParameters(
                16f, 1f, 0f, 0f, false,
                Color.white, Color.white, Color.clear);
            UIVertex vertex = UIVertex.simpleVert;
            vertex.position = rect.min;

            RoundedPanelMeshUtility.EncodeVertex(ref vertex, rect, parameters);

            Assert.That(vertex.uv2.z, Is.EqualTo(5f));
        }

        [Test]
        public void Apply_ReusesComponentAndClampsPublicParameters()
        {
            var gameObject = new GameObject("Panel", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            try
            {
                Image image = gameObject.GetComponent<Image>();
                RoundedPanel first = RoundedPanel.Apply(image, 16f, true, 0.55f);
                RoundedPanel second = RoundedPanel.Apply(image, -4f, false, 2f);

                Assert.That(second, Is.SameAs(first));
                Assert.That(second.RadiusMm, Is.Zero);
                Assert.That(second.GlowEnabled, Is.False);
                Assert.That(second.GlowStrength, Is.Zero);
            }
            finally
            {
                Object.DestroyImmediate(gameObject);
            }
        }

        [Test]
        public void RefreshFromTheme_UsesPanelTokensAndSoftShadowFlag()
        {
            var gameObject = new GameObject("Panel", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            ThemeTokens tokens = ScriptableObject.CreateInstance<ThemeTokens>();
            try
            {
                tokens.panelBg = new Color(0.9f, 0.8f, 0.7f, 0.94f);
                tokens.panelBorder = Color.cyan;
                tokens.panelGlow = Color.magenta;
                tokens.useSoftShadowInsteadOfGlow = true;
                RoundedPanel panel = RoundedPanel.Apply(gameObject.GetComponent<Image>(), 16f, true, 0.35f);

                panel.RefreshFromTheme(tokens);
                RoundedPanelParameters parameters = panel.CurrentParameters();

                Assert.That(parameters.FillColor, Is.EqualTo(tokens.panelBg));
                Assert.That(parameters.BorderColor, Is.EqualTo(tokens.panelBorder));
                Assert.That(parameters.UseSoftShadow, Is.True);
                Assert.That(parameters.EffectColor, Is.EqualTo(Color.black));
            }
            finally
            {
                Object.DestroyImmediate(tokens);
                Object.DestroyImmediate(gameObject);
            }
        }
    }
}
