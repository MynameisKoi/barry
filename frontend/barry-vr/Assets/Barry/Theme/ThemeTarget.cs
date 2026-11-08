using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.Theme
{
    /// <summary>Tag a Graphic (Image/RawImage/TMP text) with a colour role, optionally a font role.</summary>
    [DisallowMultipleComponent]
    public class ThemeTarget : MonoBehaviour
    {
        public ThemeRole colorRole;
        public bool applyColor = true;
        public bool applyFont;
        public FontRole fontRole;

        void OnEnable()
        {
            if (ThemeApplier.Current != null) Refresh(ThemeApplier.Current);
        }

        public void Refresh(ThemeTokens k)
        {
            if (applyColor && TryGetComponent(out Graphic g)) g.color = ThemeApplier.Resolve(k, colorRole);
            if (applyFont && TryGetComponent(out TMP_Text txt))
            {
                var f = fontRole == FontRole.Mono ? k.fontMono : fontRole == FontRole.Brand ? k.fontBrand : k.fontBody;
                if (f != null) txt.font = f;
            }
        }
    }
}
