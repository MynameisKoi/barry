using System;
using Barry.Theme;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.UI.Styling
{
    public readonly struct RoundedPanelParameters
    {
        public readonly float Radius;
        public readonly float BorderWidth;
        public readonly float EffectSize;
        public readonly float EffectStrength;
        public readonly bool UseSoftShadow;
        public readonly Color FillColor;
        public readonly Color BorderColor;
        public readonly Color EffectColor;

        public RoundedPanelParameters(
            float radius,
            float borderWidth,
            float effectSize,
            float effectStrength,
            bool useSoftShadow,
            Color fillColor,
            Color borderColor,
            Color effectColor)
        {
            Radius = Mathf.Max(0f, radius);
            BorderWidth = Mathf.Max(0f, borderWidth);
            EffectSize = Mathf.Max(0f, effectSize);
            EffectStrength = Mathf.Clamp01(effectStrength);
            UseSoftShadow = useSoftShadow;
            FillColor = fillColor;
            BorderColor = borderColor;
            EffectColor = effectColor;
        }

        public Rect ExpandedRect(Rect rect)
        {
            float padding = EffectStrength > 0f ? EffectSize : 0f;
            return new Rect(
                rect.xMin - padding,
                rect.yMin - padding,
                rect.width + padding * 2f,
                rect.height + padding * 2f);
        }
    }

    public static class RoundedPanelMeshUtility
    {
        public static void EncodeVertex(
            ref UIVertex vertex,
            Rect originalRect,
            RoundedPanelParameters parameters)
        {
            float padding = parameters.EffectStrength > 0f ? parameters.EffectSize : 0f;
            Vector3 position = vertex.position;
            if (Mathf.Approximately(position.x, originalRect.xMin)) position.x -= padding;
            else if (Mathf.Approximately(position.x, originalRect.xMax)) position.x += padding;
            if (Mathf.Approximately(position.y, originalRect.yMin)) position.y -= padding;
            else if (Mathf.Approximately(position.y, originalRect.yMax)) position.y += padding;
            vertex.position = position;

            Vector2 centre = originalRect.center;
            vertex.uv1 = new Vector4(position.x - centre.x, position.y - centre.y, 0f, 0f);
            vertex.uv2 = new Vector4(
                originalRect.width * 0.5f,
                originalRect.height * 0.5f,
                Mathf.Min(parameters.Radius, Mathf.Min(originalRect.width, originalRect.height) * 0.5f),
                parameters.BorderWidth);
            vertex.uv3 = new Vector4(
                parameters.EffectSize,
                parameters.EffectStrength,
                parameters.BorderColor.a,
                parameters.UseSoftShadow ? 1f : 0f);
            vertex.color = parameters.FillColor;
            vertex.normal = new Vector3(
                parameters.BorderColor.r,
                parameters.BorderColor.g,
                parameters.BorderColor.b);
            vertex.tangent = parameters.EffectColor;
        }
    }

    [DisallowMultipleComponent]
    [RequireComponent(typeof(Image))]
    public sealed class RoundedPanel : BaseMeshEffect
    {
        public const float DefaultPanelRadiusMm = 16f;
        public const float DefaultBorderWidthMm = 1f;
        public const float DefaultGlowSizeMm = 32f;
        public const float DefaultGlowStrength = 0.35f;
        public const float CameraGlowStrength = 0.55f;

        private static readonly Color DefaultFill = new Color(1f, 1f, 1f, 0.94f);
        private static readonly Color DefaultBorder = new Color(0.788f, 0.745f, 1f, 1f);
        private static readonly Color DefaultGlow = new Color(0.486f, 0.361f, 1f, 1f);
        private static Material sharedMaterial;

        [SerializeField, Min(0f)] private float radiusMm = DefaultPanelRadiusMm;
        [SerializeField, Min(0f)] private float borderWidthMm = DefaultBorderWidthMm;
        [SerializeField, Min(0f)] private float glowSizeMm = DefaultGlowSizeMm;
        [SerializeField, Range(0f, 1f)] private float glowStrength = DefaultGlowStrength;
        [SerializeField] private bool glow = true;

        private Color fillColor = DefaultFill;
        private Color borderColor = DefaultBorder;
        private Color effectColor = DefaultGlow;
        private bool useSoftShadow;
        private bool customColors;

        public float RadiusMm => radiusMm;
        public float BorderWidthMm => borderWidthMm;
        public float GlowSizeMm => glowSizeMm;
        public float GlowStrength => glow ? glowStrength : 0f;
        public bool GlowEnabled => glow;
        public bool UsesSoftShadow => useSoftShadow;

        protected override void OnEnable()
        {
            base.OnEnable();
            ThemeApplier.Changed += RefreshFromTheme;
            EnsureGraphicSetup();
            RefreshFromTheme(ThemeApplier.Current);
        }

        protected override void OnDisable()
        {
            ThemeApplier.Changed -= RefreshFromTheme;
            base.OnDisable();
        }

        public override void ModifyMesh(VertexHelper vertexHelper)
        {
            if (!IsActive() || vertexHelper.currentVertCount == 0) return;

            Rect bounds = ReadBounds(vertexHelper);
            RoundedPanelParameters parameters = CurrentParameters();
            UIVertex vertex = default;
            for (int index = 0; index < vertexHelper.currentVertCount; index++)
            {
                vertexHelper.PopulateUIVertex(ref vertex, index);
                RoundedPanelMeshUtility.EncodeVertex(ref vertex, bounds, parameters);
                vertexHelper.SetUIVertex(vertex, index);
            }
        }

        public static RoundedPanel Apply(Image image, float radiusMm, bool glow, float glowStrength)
        {
            if (image == null) throw new ArgumentNullException(nameof(image));
            RoundedPanel panel = image.GetComponent<RoundedPanel>();
            if (panel == null) panel = image.gameObject.AddComponent<RoundedPanel>();
            panel.radiusMm = Mathf.Max(0f, radiusMm);
            panel.glow = glow;
            panel.glowStrength = Mathf.Clamp01(glowStrength);
            panel.EnsureGraphicSetup();
            panel.RefreshFromTheme(ThemeApplier.Current);
            panel.graphic.SetVerticesDirty();
            return panel;
        }

        /// <summary>
        /// Override fill/border with explicit colours (chips, buttons, tracks). Survives theme changes;
        /// the glow/shadow still follows the theme. Additive API used by Barry.UI.PanelSkin.
        /// </summary>
        public void SetColors(Color fill, Color border)
        {
            customColors = true;
            fillColor = fill;
            borderColor = border;
            if (graphic != null) graphic.SetVerticesDirty();
        }

        public void SetBorderWidth(float widthMm)
        {
            borderWidthMm = Mathf.Max(0f, widthMm);
            if (graphic != null) graphic.SetVerticesDirty();
        }

        public void SetRadius(float radius)
        {
            radiusMm = Mathf.Max(0f, radius);
            if (graphic != null) graphic.SetVerticesDirty();
        }

        public void SetStyle(float radius, float borderWidth, float effectSize, Color border, Color effect)
        {
            radiusMm = Mathf.Max(0f, radius);
            borderWidthMm = Mathf.Max(0f, borderWidth);
            glowSizeMm = Mathf.Max(0f, effectSize);
            borderColor = border;
            effectColor = effect;
            if (graphic != null) graphic.SetVerticesDirty();
        }

        public void RefreshFromTheme(ThemeTokens tokens)
        {
            if (tokens == null)
            {
                if (!customColors)
                {
                    fillColor = DefaultFill;
                    borderColor = DefaultBorder;
                }
                effectColor = DefaultGlow;
                useSoftShadow = false;
            }
            else
            {
                if (!customColors)
                {
                    fillColor = tokens.panelBg;
                    borderColor = tokens.panelBorder;
                }
                useSoftShadow = tokens.useSoftShadowInsteadOfGlow;
                effectColor = useSoftShadow ? Color.black : tokens.panelGlow;
                effectColor.a = 1f;
            }
            if (graphic != null) graphic.SetVerticesDirty();
        }

        public RoundedPanelParameters CurrentParameters() => new RoundedPanelParameters(
            radiusMm,
            borderWidthMm,
            glowSizeMm,
            glow ? glowStrength : 0f,
            useSoftShadow,
            fillColor,
            borderColor,
            effectColor);

        private void EnsureGraphicSetup()
        {
            if (graphic is Image image)
            {
                image.type = Image.Type.Simple;
                image.material = GetSharedMaterial();
            }

            Canvas canvas = graphic != null ? graphic.canvas : null;
            if (canvas != null)
            {
                canvas.additionalShaderChannels |= AdditionalCanvasShaderChannels.TexCoord1
                    | AdditionalCanvasShaderChannels.TexCoord2
                    | AdditionalCanvasShaderChannels.TexCoord3
                    | AdditionalCanvasShaderChannels.Normal
                    | AdditionalCanvasShaderChannels.Tangent;
            }
        }

        private static Material GetSharedMaterial()
        {
            if (sharedMaterial != null) return sharedMaterial;
            sharedMaterial = Resources.Load<Material>("BarryRoundedPanel");
            if (sharedMaterial == null)
                throw new InvalidOperationException(
                    "Resources/BarryRoundedPanel.mat was not found. The shared material is required in player builds.");
            return sharedMaterial;
        }

        private static Rect ReadBounds(VertexHelper vertexHelper)
        {
            UIVertex vertex = default;
            vertexHelper.PopulateUIVertex(ref vertex, 0);
            float minX = vertex.position.x;
            float maxX = minX;
            float minY = vertex.position.y;
            float maxY = minY;
            for (int index = 1; index < vertexHelper.currentVertCount; index++)
            {
                vertexHelper.PopulateUIVertex(ref vertex, index);
                minX = Mathf.Min(minX, vertex.position.x);
                maxX = Mathf.Max(maxX, vertex.position.x);
                minY = Mathf.Min(minY, vertex.position.y);
                maxY = Mathf.Max(maxY, vertex.position.y);
            }
            return Rect.MinMaxRect(minX, minY, maxX, maxY);
        }
    }
}
