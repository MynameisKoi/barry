using TMPro;
using UnityEngine;

namespace Barry.Theme
{
    /// <summary>Design tokens from HANDOFF §5. One asset per theme.</summary>
    [CreateAssetMenu(menuName = "Barry/Theme Tokens", fileName = "ThemeTokens")]
    public class ThemeTokens : ScriptableObject
    {
        public Barry.Config.ThemeId id;

        [Header("Environment")]
        public Color envSky;
        public Color nebulaA;           // alpha = start opacity, fades to 0
        public Color nebulaB;
        public Color planetLight, planetMid, planetDark, planetRing;
        public Color starColor = Color.white;
        [Range(0, 1)] public float starMinAlpha = 0.35f;
        public Color floor, floorGrid;  // grid alpha carries the 28 %
        public Color platformFill, platformRim, platformRingDashed;

        [Header("Panels")]
        public Color panelBg, panelBorder, panelGlow;
        [Range(0, 1)] public float cameraGlowAlpha = 0.55f;
        public bool useSoftShadowInsteadOfGlow;

        [Header("Text")]
        public Color textPrimary, textSecondary, brand;

        [Header("Accent")]
        public Color accent, accentSoft;
        public Color rayCore, rayGlow, cursorFill = Color.white;

        [Header("Status")]
        public Color okText, okBg;
        public Color warnText, warnBg, warnBorder;
        public Color dangerSolid, dangerText, dangerSoft;
        public Color stopText = Color.white;   // STOP is always solid danger + white text
        public Color neutralFill, neutralBorder;
        public Color videoBg;
        public Color overlayDim;               // #080A22 @ 72 %

        [Header("Fonts (TMP)")]
        public TMP_FontAsset fontBody;     // Inter
        public TMP_FontAsset fontMono;     // JetBrains Mono
        public TMP_FontAsset fontBrand;    // Orbitron
    }
}
