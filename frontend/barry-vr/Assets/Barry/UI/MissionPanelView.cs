using System;
using Barry.Theme;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.UI
{
    /// <summary>The start-mission card: operator picker (prev/next), zone presets (prev/next), Start, and the hint line.</summary>
    public sealed class MissionPanelView : MonoBehaviour
    {
        [SerializeField] GameObject content;
        [SerializeField] TMP_Text hint;
        [SerializeField] TMP_Text operatorText;
        [SerializeField] TMP_Text zoneText;
        [SerializeField] TMP_Text error;
        [SerializeField] Button operatorPrev, operatorNext, zonePrev, zoneNext, startButton;
        [SerializeField] TMP_Text startLabel;

        MissionPanelModel last;

        public event Action<int> OperatorStep;
        public event Action<int> ZoneStep;
        public event Action StartClicked;
        public bool Visible => content != null && content.activeSelf;

        void Awake()
        {
            operatorPrev.onClick.AddListener(() => OperatorStep?.Invoke(-1));
            operatorNext.onClick.AddListener(() => OperatorStep?.Invoke(1));
            zonePrev.onClick.AddListener(() => ZoneStep?.Invoke(-1));
            zoneNext.onClick.AddListener(() => ZoneStep?.Invoke(1));
            startButton.onClick.AddListener(() => StartClicked?.Invoke());
            if (content != null) content.SetActive(false);
        }

        void OnEnable() { ThemeApplier.Changed += OnTheme; Render(); }
        void OnDisable() { ThemeApplier.Changed -= OnTheme; }
        void OnTheme(ThemeTokens _) => Render();

        public void Bind(MissionPanelModel model) { last = model; Render(); }

        void Render()
        {
            if (last == null || content == null) return;
            if (content.activeSelf != last.Visible) content.SetActive(last.Visible);
            if (!last.Visible) return;
            if (hint != null)
            {
                hint.text = last.HintText;
                hint.gameObject.SetActive(!string.IsNullOrEmpty(last.HintText));
            }
            operatorText.text = last.OperatorText;
            zoneText.text = last.SelectedZone;
            if (error != null) error.text = last.StartError ?? string.Empty;
            startLabel.text = last.StartButtonLabel;
            startButton.interactable = last.StartEnabled;
            bool pickable = last.HasOperators && !last.Starting;
            operatorPrev.interactable = operatorNext.interactable = pickable;
            zonePrev.interactable = zoneNext.interactable = !last.Starting;
        }
    }
}
