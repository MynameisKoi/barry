using Barry.Theme;
using Barry.UI.Styling;
using UnityEngine;

namespace Barry.UI
{
    /// <summary>
    /// Fill/border for a RoundedPanel. Colours are serialized (so Edit mode renders correctly) and can be
    /// bound to theme roles so they follow Galactic/Light. Runtime views call SetColors for state changes.
    /// </summary>
    [ExecuteAlways, DisallowMultipleComponent, RequireComponent(typeof(RoundedPanel))]
    public sealed class PanelSkin : MonoBehaviour
    {
        [SerializeField] Color fill = Color.white;
        [SerializeField] Color border = Color.clear;
        [SerializeField] float borderWidthMm = 1f;
        [SerializeField] bool fillFollowsTheme;
        [SerializeField] bool borderFollowsTheme;
        [SerializeField] ThemeRole fillRole;
        [SerializeField] ThemeRole borderRole;
        RoundedPanel panel;

        public Color Fill => fill;
        public Color Border => border;

        public void Configure(Color fillColor, Color borderColor, float borderMm,
            ThemeRole? fillThemeRole = null, ThemeRole? borderThemeRole = null)
        {
            fill = fillColor; border = borderColor; borderWidthMm = borderMm;
            fillFollowsTheme = fillThemeRole.HasValue; if (fillThemeRole.HasValue) fillRole = fillThemeRole.Value;
            borderFollowsTheme = borderThemeRole.HasValue; if (borderThemeRole.HasValue) borderRole = borderThemeRole.Value;
            Apply();
        }

        /// <summary>Runtime state colour (e.g. selected segment). Stops following the theme.</summary>
        public void SetColors(Color fillColor, Color borderColor, float? borderMm = null)
        {
            fill = fillColor; border = borderColor;
            fillFollowsTheme = borderFollowsTheme = false;
            if (borderMm.HasValue) borderWidthMm = borderMm.Value;
            Apply();
        }

        void OnEnable()
        {
            ThemeApplier.Changed += OnTheme;
            if (ThemeApplier.Current != null) Resolve(ThemeApplier.Current);
            Apply();
        }

        void OnDisable() { ThemeApplier.Changed -= OnTheme; }

        void OnTheme(ThemeTokens k) { Resolve(k); Apply(); }

        void Resolve(ThemeTokens k)
        {
            if (fillFollowsTheme) fill = ThemeApplier.Resolve(k, fillRole);
            if (borderFollowsTheme) border = ThemeApplier.Resolve(k, borderRole);
        }

        void Apply()
        {
            if (panel == null) panel = GetComponent<RoundedPanel>();
            if (panel == null) return;
            panel.SetColors(fill, border);
            panel.SetBorderWidth(borderWidthMm);
        }
    }
}
