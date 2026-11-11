using System;
using Barry.Theme;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.UI
{
    /// <summary>The brain panel: preset goals, live steps, the answer, Cancel. Collapsed it is just a "BRAIN · Y" tab.</summary>
    public sealed class LlmPanelView : MonoBehaviour
    {
        [SerializeField] GameObject tab;
        [SerializeField] GameObject content;
        [SerializeField] Button[] presetButtons;
        [SerializeField] ChipView stateChip;
        [SerializeField] TMP_Text[] rowLabels;
        [SerializeField] TMP_Text[] rowStates;
        [SerializeField] TMP_Text moreLabel;
        [SerializeField] TMP_Text answer;
        [SerializeField] TMP_Text notice;
        [SerializeField] Button cancelButton;
        [SerializeField] TMP_Text cancelLabel;

        LlmPanelModel last;

        public event Action<int> PresetChosen;
        public event Action CancelClicked;
        public bool Visible => content != null && content.activeSelf;

        void Awake()
        {
            for (int i = 0; i < presetButtons.Length; i++)
            {
                int index = i;
                presetButtons[i].onClick.AddListener(() => PresetChosen?.Invoke(index));
            }
            cancelButton.onClick.AddListener(() => CancelClicked?.Invoke());
            content.SetActive(false);
        }

        void OnEnable() { ThemeApplier.Changed += OnTheme; Render(); }
        void OnDisable() { ThemeApplier.Changed -= OnTheme; }
        void OnTheme(ThemeTokens _) => Render();

        public void Bind(LlmPanelModel model) { last = model; Render(); }

        void Render()
        {
            if (last == null || content == null) return;
            if (tab != null && tab.activeSelf == last.Open) tab.SetActive(!last.Open);
            if (content.activeSelf != last.Open) content.SetActive(last.Open);
            if (!last.Open) return;

            var k = ThemeApplier.Current;
            stateChip.Set(last.Chip);
            foreach (var b in presetButtons) b.interactable = last.PresetsEnabled;

            for (int i = 0; i < rowLabels.Length; i++)
            {
                bool has = i < last.Rows.Count;
                rowLabels[i].gameObject.SetActive(has);
                rowStates[i].gameObject.SetActive(has);
                if (!has) continue;
                LlmStepRow row = last.Rows[i];
                rowLabels[i].text = row.Text;
                rowStates[i].text = row.StateWord;
                if (k != null)
                {
                    ToneColors.Resolve(row.Tone, k, out var fg, out _);
                    rowStates[i].color = fg;
                }
            }
            if (moreLabel != null)
            {
                moreLabel.gameObject.SetActive(last.HiddenSteps > 0);
                moreLabel.text = "+" + last.HiddenSteps + " earlier";
            }

            answer.text = last.AnswerText;
            if (k != null)
            {
                ToneColors.Resolve(last.AnswerTone, k, out var afg, out _);
                answer.color = last.AnswerTone == StatusTone.Neutral ? k.textPrimary : afg;
            }
            notice.text = last.Notice;
            cancelButton.gameObject.SetActive(last.CancelVisible);
            cancelButton.interactable = last.CancelEnabled;
            cancelLabel.text = last.CancelText;
        }
    }
}
