using UnityEngine;

namespace Barry.Config
{
    public enum ThemeId { Galactic, Light }
    public enum TextSize { Small, Medium, Large }
    public enum RobotTarget { Simulator, RealRobot }

    /// <summary>Non-secret runtime settings (HANDOFF §9.5). Never store passwords or API keys here.</summary>
    [CreateAssetMenu(menuName = "Barry/Config", fileName = "BarryConfig")]
    public class BarryConfig : ScriptableObject
    {
        public const string PrefsKey = "barry.config.v1";

        [Header("Connection")]
        public string backendUrl = "http://192.168.1.10:8000";
        public string cameraStreamUrl = "http://192.168.1.10:8000/api/v1/camera/stream";
        public RobotTarget target = RobotTarget.Simulator;

        [Header("Control")]
        [Range(0f, 0.30f)] public float deadZone = 0.12f;
        [Range(5, 20)] public int sendRateHz = 10;

        [Header("Display")]
        [Range(1.5f, 3.0f)] public float cameraDistance = 2.0f;
        public TextSize textSize = TextSize.Medium;
        public ThemeId theme = ThemeId.Galactic;

        /// <summary>Persist the non-secret values with PlayerPrefs.</summary>
        public void Save() => PlayerPrefs.SetString(PrefsKey, JsonUtility.ToJson(this));

        /// <summary>Overlay saved values (if any) onto this instance.</summary>
        public void Load()
        {
            if (PlayerPrefs.HasKey(PrefsKey))
                JsonUtility.FromJsonOverwrite(PlayerPrefs.GetString(PrefsKey), this);
            Sanitize();
        }

        public void Sanitize()
        {
            deadZone = Mathf.Clamp(deadZone, 0f, 0.30f);
            sendRateHz = Mathf.Clamp(sendRateHz, 5, 20);
            cameraDistance = Mathf.Clamp(cameraDistance, 1.5f, 3.0f);
        }

        void OnValidate() => Sanitize();
    }
}
