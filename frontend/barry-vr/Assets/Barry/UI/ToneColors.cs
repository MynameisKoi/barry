using Barry.Theme;
using UnityEngine;

namespace Barry.UI
{
    /// <summary>Maps a StatusTone to theme colours. Status is always paired with words, never colour alone.</summary>
    public static class ToneColors
    {
        public static void Resolve(StatusTone tone, ThemeTokens k, out Color fg, out Color bg)
        {
            switch (tone)
            {
                case StatusTone.Ok: fg = k.okText; bg = k.okBg; break;
                case StatusTone.Warning: fg = k.warnText; bg = k.warnBg; break;
                case StatusTone.Danger: fg = k.dangerText; bg = k.dangerSoft; break;
                case StatusTone.Accent: fg = k.accent; bg = k.accentSoft; break;
                default: fg = k.textPrimary; bg = k.neutralFill; break;
            }
        }
    }
}
