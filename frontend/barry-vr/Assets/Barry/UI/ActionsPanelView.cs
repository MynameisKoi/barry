using System;
using System.Collections.Generic;
using Barry.Theme;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace Barry.UI
{
    /// <summary>Actions list from /actions. Collapsed state shows only the "ACTIONS · B" tab.</summary>
    public sealed class ActionsPanelView : MonoBehaviour
    {
        [SerializeField] GameObject expanded;
        [SerializeField] GameObject collapsedTab;
        [SerializeField] TMP_Text collapsedLabel;
        [SerializeField] RectTransform listRoot;
        [SerializeField] Button itemTemplate;      // inactive template, cloned per action
        [SerializeField] TMP_Text footer;
        [SerializeField] TMP_Text emptyLabel;   // muted 'No actions available' when open with no actions

        [SerializeField] float mmPerMockupPx = 1.6667f;
        float BorderMm(float px) => px * mmPerMockupPx;

        readonly List<Button> spawned = new List<Button>();
        ActionsPanelModel model;

        public event Action<int> ActionChosen;
        public event Action<int?> ActionHovered;

        void OnEnable() { ThemeApplier.Changed += OnTheme; }
        void OnDisable() { ThemeApplier.Changed -= OnTheme; }
        void OnTheme(ThemeTokens _) { if (model != null) Bind(model); }

        public void Bind(ActionsPanelModel m)
        {
            model = m;
            expanded.SetActive(!m.IsCollapsed);
            collapsedTab.SetActive(m.IsCollapsed);
            collapsedLabel.text = ActionsPanelModel.CollapsedTab;
            footer.text = ActionsPanelModel.Footer;

            foreach (var b in spawned) if (b != null) Destroy(b.gameObject);
            spawned.Clear();
            if (emptyLabel != null) emptyLabel.gameObject.SetActive(!m.IsCollapsed && m.Items.Count == 0);
            if (m.IsCollapsed) return;

            var k = ThemeApplier.Current;
            foreach (var item in m.Items)
            {
                var b = Instantiate(itemTemplate, listRoot);
                b.gameObject.SetActive(true);
                int id = item.Id;
                var label = b.GetComponentInChildren<TMP_Text>();
                label.text = item.IsRunning ? item.Name + " · running…" : item.Name;
                b.interactable = item.IsEnabled;
                var skin = b.GetComponent<PanelSkin>();
                if (k != null)
                {
                    // Mockup: normal #F7F5F0/#DAD5CB 1px; hovered accentSoft + 2px accent; running #F3F1EC dashed-style #CFCAC0.
                    if (item.IsHovered) skin.SetColors(k.accentSoft, k.accent, BorderMm(2f));
                    else if (item.IsRunning) skin.SetColors(new Color32(0xF3, 0xF1, 0xEC, 255), new Color32(0xCF, 0xCA, 0xC0, 255), BorderMm(1f));
                    else skin.SetColors(new Color32(0xF7, 0xF5, 0xF0, 255), k.neutralBorder, BorderMm(1f));
                    label.color = item.IsRunning || !item.IsEnabled ? (Color)new Color32(0x6B, 0x6A, 0x65, 255) : k.textPrimary;
                    label.fontStyle = item.IsHovered ? FontStyles.Bold : FontStyles.Normal;
                }
                b.onClick.AddListener(() => ActionChosen?.Invoke(id));
                var trig = b.gameObject.AddComponent<EventTrigger>();
                AddTrigger(trig, EventTriggerType.PointerEnter, () => ActionHovered?.Invoke(id));
                AddTrigger(trig, EventTriggerType.PointerExit, () => ActionHovered?.Invoke(null));
                spawned.Add(b);
            }
        }

        static void AddTrigger(EventTrigger t, EventTriggerType type, Action a)
        {
            var e = new EventTrigger.Entry { eventID = type };
            e.callback.AddListener(_ => a());
            t.triggers.Add(e);
        }
    }
}
