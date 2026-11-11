using TMPro;
using UnityEngine;

namespace Barry.UI
{
    public sealed class HeaderStripView : MonoBehaviour
    {
        [SerializeField] TMP_Text modeLabel;
        [SerializeField] ChipView robotChip;
        [SerializeField] ChipView controlChip;
        [SerializeField] ChipView missionChip;     // MISSION · zone · operator (or NO MISSION)
        [SerializeField] ChipView emergencyChip;   // "E-STOP LATCHED", hidden unless latched

        public void Bind(HeaderChipsModel model)
        {
            robotChip.Set(model.Robot);
            controlChip.Set(model.Control);
        }

        public void SetMission(HeaderChipModel chip)
        {
            if (missionChip != null) missionChip.Set(chip);
        }

        public void SetEmergency(EmergencyHudModel model)
        {
            if (emergencyChip == null) return;
            emergencyChip.gameObject.SetActive(model.Visible);
            if (model.Visible) emergencyChip.Set(model.Chip, model.Tone);
        }

        public void SetMode(string text) { if (modeLabel != null) modeLabel.text = text; }
    }
}
