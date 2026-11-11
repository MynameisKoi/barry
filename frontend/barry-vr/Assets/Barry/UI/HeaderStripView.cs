using TMPro;
using UnityEngine;

namespace Barry.UI
{
    public sealed class HeaderStripView : MonoBehaviour
    {
        [SerializeField] TMP_Text modeLabel;
        [SerializeField] ChipView robotChip;
        [SerializeField] ChipView controlChip;
        [SerializeField] ChipView emergencyChip;   // "E-STOP LATCHED", hidden unless latched
        [SerializeField] GameObject modePill;      // MODE · MANUAL pill; hidden while latched: the header has no room for both

        public void Bind(HeaderChipsModel model)
        {
            robotChip.Set(model.Robot);
            controlChip.Set(model.Control);
        }

        public void SetEmergency(EmergencyHudModel model)
        {
            if (emergencyChip == null) return;
            emergencyChip.gameObject.SetActive(model.Visible);
            if (modePill != null) modePill.SetActive(!model.Visible);
            if (model.Visible) emergencyChip.Set(model.Chip, model.Tone);
        }

        public void SetMode(string text) { if (modeLabel != null) modeLabel.text = text; }
    }
}
