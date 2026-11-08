using Barry.Api;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.UI
{
    /// <summary>Camera screen: RawImage fed by MjpegStreamSource.Texture; height follows the stream aspect.</summary>
    public sealed class CameraScreenView : MonoBehaviour
    {
        [SerializeField] RawImage video;
        [SerializeField] RectTransform screenRect;   // bezel+video container; width fixed, height from aspect
        [SerializeField] float bezelMm = 20f;
        [SerializeField] TMP_Text fpsLabel;
        [SerializeField] TMP_Text ageLabel;
        [SerializeField] TMP_Text rollPitchLabel;
        [SerializeField] ChipView liveChip;
        [SerializeField] GameObject placeholder;
        MjpegStreamSource source;

        public void Bind(MjpegStreamSource stream)
        {
            if (source != null) source.FrameReady -= OnFrame;
            source = stream;
            if (source != null) source.FrameReady += OnFrame;
        }

        void OnDestroy() { if (source != null) source.FrameReady -= OnFrame; }

        void OnFrame(Texture2D tex)
        {
            if (tex == null || tex.width <= 0) return;
            if (video.texture != tex) video.texture = tex;
            video.color = Color.white;
            if (placeholder != null) placeholder.SetActive(false);
            float aspect = (float)tex.height / tex.width;
            float w = screenRect.rect.width;
            screenRect.SetSizeWithCurrentAnchors(RectTransform.Axis.Vertical, (w - 2 * bezelMm) * aspect + 2 * bezelMm);
        }

        void Update()
        {
            if (source == null) return;
            bool has = !float.IsInfinity(source.FrameAgeSeconds);
            fpsLabel.text = has && source.FramesPerSecond > 0 ? source.FramesPerSecond.ToString("0") + " fps" : "-- fps";
            bool live = has && source.FrameAgeSeconds < 2f;
            if (liveChip != null) liveChip.Set(live ? "LIVE" : "NO SIGNAL", live ? StatusTone.Ok : StatusTone.Danger);
            ageLabel.text = has ? "frame age " + Mathf.RoundToInt(source.FrameAgeSeconds * 1000f) + " ms" : "frame age -- ms";
        }

        public void SetOrientation(TelemetryResponse t)
        {
            bool ok = t != null && t.orientation != null;
            rollPitchLabel.text = ok
                ? "ROLL " + t.orientation.roll.ToString("0") + "° · PITCH " + t.orientation.pitch.ToString("0") + "°"
                : "ROLL --° · PITCH --°";
        }
    }
}
