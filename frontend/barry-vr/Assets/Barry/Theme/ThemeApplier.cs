using System;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.Theme
{
    public enum ThemeRole
    {
        PanelBg, PanelBorder, Accent, AccentSoft, NeutralFill, NeutralBorder,
        Danger, DangerSoft, Ok, OkBg, Warn, WarnBg, VideoBg, OverlayDim,
        TextPrimary, TextSecondary, Brand, StopText, TextOnDanger = StopText
    }

    public enum FontRole { Body, Mono, Brand }

    /// <summary>Applies a ThemeTokens asset to tagged Graphics/TMP texts under this object.</summary>
    public class ThemeApplier : MonoBehaviour
    {
        public static event Action<ThemeTokens> Changed;
        public static ThemeTokens Current { get; private set; }

        [SerializeField] ThemeTokens galactic;
        [SerializeField] ThemeTokens light;
        [SerializeField] Barry.Config.BarryConfig config;

        void OnEnable()
        {
            if (config != null) Apply(config.theme);
        }

        public void Apply(Barry.Config.ThemeId id) => Apply(id == Barry.Config.ThemeId.Light ? light : galactic);

        public void Apply(ThemeTokens tokens)
        {
            if (tokens == null) return;
            Current = tokens;
            foreach (var t in FindObjectsByType<ThemeTarget>(FindObjectsInactive.Include))
                t.Refresh(tokens);
            Changed?.Invoke(tokens);
        }

        public static Color Resolve(ThemeTokens k, ThemeRole r) => r switch
        {
            ThemeRole.PanelBg => k.panelBg,
            ThemeRole.PanelBorder => k.panelBorder,
            ThemeRole.Accent => k.accent,
            ThemeRole.AccentSoft => k.accentSoft,
            ThemeRole.NeutralFill => k.neutralFill,
            ThemeRole.NeutralBorder => k.neutralBorder,
            ThemeRole.Danger => k.dangerSolid,
            ThemeRole.DangerSoft => k.dangerSoft,
            ThemeRole.Ok => k.okText,
            ThemeRole.OkBg => k.okBg,
            ThemeRole.Warn => k.warnText,
            ThemeRole.WarnBg => k.warnBg,
            ThemeRole.VideoBg => k.videoBg,
            ThemeRole.OverlayDim => k.overlayDim,
            ThemeRole.TextPrimary => k.textPrimary,
            ThemeRole.TextSecondary => k.textSecondary,
            ThemeRole.Brand => k.brand,
            ThemeRole.StopText => k.stopText,
            _ => Color.magenta,
        };
    }
}
