using TMPro;
using UnityEngine;

namespace Barry.UI
{
    public sealed class HeaderStripView : MonoBehaviour
    {
        [SerializeField] TMP_Text modeLabel;
        [SerializeField] ChipView robotChip;
        [SerializeField] ChipView controlChip;

        public void Bind(HeaderChipsModel model)
        {
            robotChip.Set(model.Robot);
            controlChip.Set(model.Control);
        }

        public void SetMode(string text) { if (modeLabel != null) modeLabel.text = text; }
    }
}
