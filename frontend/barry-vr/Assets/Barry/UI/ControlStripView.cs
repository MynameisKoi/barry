using System;
using Barry.Theme;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.UI
{
    public sealed class ControlStripView : MonoBehaviour
    {
        public static readonly string[] Levels = { "LOW", "MEDIUM", "HIGH" };

        [SerializeField] Button[] levelButtons = new Button[3];   // LOW, MEDIUM, HIGH
        [SerializeField] Button stopButton;
        [SerializeField] Button exitButton;
        [SerializeField] TMP_Text stopHint;

        string selected = "MEDIUM";

        public event Action<string> IntensityChosen;
        public event Action StopClicked;
        public event Action ExitClicked;

        void Awake()
        {
            for (int i = 0; i < levelButtons.Length; i++)
            {
                string level = Levels[i];
                levelButtons[i].onClick.AddListener(() => IntensityChosen?.Invoke(level));
            }
            stopButton.onClick.AddListener(() => StopClicked?.Invoke());
            exitButton.onClick.AddListener(() => ExitClicked?.Invoke());
            if (stopHint != null) stopHint.text = "A button · or point + trigger";
        }

        void OnEnable() { ThemeApplier.Changed += OnTheme; Render(); }
        void OnDisable() { ThemeApplier.Changed -= OnTheme; }
        void OnTheme(ThemeTokens _) => Render();

        public void SetIntensity(string level) { selected = level; Render(); }

        void Render()
        {
            var k = ThemeApplier.Current;
            if (k == null) return;
            for (int i = 0; i < levelButtons.Length; i++)
            {
                bool on = Levels[i] == selected;
                var skin = levelButtons[i].GetComponent<PanelSkin>();
                // Mockup: selected = accent/white, others #F3F1EC/#3D3D3A with #DAD5CB border.
                if (on) skin.SetColors(k.accent, k.accent);
                else skin.SetColors(k.neutralFill, k.neutralBorder);
                levelButtons[i].GetComponentInChildren<TMP_Text>().color = on ? Color.white : (Color)new Color32(0x3D, 0x3D, 0x3A, 255);
            }
            stopButton.GetComponent<PanelSkin>().SetColors(k.dangerSolid, Color.clear, 0f);   // always solid danger + white text
            foreach (var t in stopButton.GetComponentsInChildren<TMP_Text>(true)) t.color = k.stopText;
        }
    }
}
