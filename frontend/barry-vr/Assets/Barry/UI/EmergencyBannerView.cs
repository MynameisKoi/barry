using System;
using Barry.Theme;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.UI
{
    /// <summary>The latched-e-stop banner: shown only while latched, with the two-press reset button.</summary>
    public sealed class EmergencyBannerView : MonoBehaviour
    {
        [SerializeField] GameObject content;
        [SerializeField] ChipView chip;
        [SerializeField] TMP_Text title;
        [SerializeField] TMP_Text detail;
        [SerializeField] TMP_Text footer;
        [SerializeField] Button resetButton;
        [SerializeField] TMP_Text resetLabel;

        EmergencyHudModel last;

        public event Action ResetClicked;
        public bool Visible => content != null && content.activeSelf;

        void Awake()
        {
            if (resetButton != null) resetButton.onClick.AddListener(() => ResetClicked?.Invoke());
            if (content != null) content.SetActive(false);
        }

        void OnEnable() { ThemeApplier.Changed += OnTheme; Render(); }
        void OnDisable() { ThemeApplier.Changed -= OnTheme; }
        void OnTheme(ThemeTokens _) => Render();

        public void Bind(EmergencyHudModel model) { last = model; Render(); }

        void Render()
        {
            if (last == null || content == null) return;
            if (content.activeSelf != last.Visible) content.SetActive(last.Visible);
            if (!last.Visible) return;
            if (chip != null) chip.Set(last.Chip, last.Tone);
            if (title != null) title.text = last.TitleText;
            if (detail != null) detail.text = last.DetailText;
            if (footer != null) footer.text = last.FooterText;
            if (resetLabel != null) resetLabel.text = last.ButtonLabel;
            if (resetButton != null) resetButton.interactable = last.ButtonEnabled;
        }
    }
}
