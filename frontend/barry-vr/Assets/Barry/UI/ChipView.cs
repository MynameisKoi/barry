using Barry.Theme;
using TMPro;
using UnityEngine;

namespace Barry.UI
{
    /// <summary>A status pill: optional dot + word, coloured from a StatusTone (never colour alone).</summary>
    public sealed class ChipView : MonoBehaviour
    {
        static readonly Color OverlayWhite = new Color(1f, 1f, 1f, 0.92f);

        [SerializeField] PanelSkin skin;
        [SerializeField] PanelSkin dot;
        [SerializeField] TMP_Text label;
        [SerializeField] bool overlayStyle;   // white @92 % (camera-screen chips) instead of the tone's soft fill
        HeaderChipModel last;
        bool has;

        public void Set(HeaderChipModel model) { last = model; has = true; Render(); }
        public void Set(string text, StatusTone tone) => Set(new HeaderChipModel(text, tone));
        void OnEnable() { ThemeApplier.Changed += OnTheme; Render(); }
        void OnDisable() { ThemeApplier.Changed -= OnTheme; }
        void OnTheme(ThemeTokens _) => Render();

        void Render()
        {
            if (!has || ThemeApplier.Current == null) return;
            ToneColors.Resolve(last.Tone, ThemeApplier.Current, out var fg, out var bg);
            label.text = last.Text;
            label.color = fg;
            if (skin != null) skin.SetColors(overlayStyle ? OverlayWhite : bg, Color.clear, 0f);
            if (dot != null) dot.SetColors(fg, Color.clear, 0f);
        }
    }
}
