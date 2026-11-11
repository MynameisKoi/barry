using System;
using UnityEngine;

namespace Barry.Config
{
    public enum ThemeId { Galactic, Light }
    public enum TextSize { Small, Medium, Large }
    public enum RobotTarget { Simulator, RealRobot }

    /// <summary>Runtime settings. API keys are build-time values and are never persisted with PlayerPrefs.</summary>
    [CreateAssetMenu(menuName = "Barry/Config", fileName = "BarryConfig")]
    public class BarryConfig : ScriptableObject
    {
        [Serializable]
        private sealed class SavedSettings
        {
            public string backendUrl;
            public string cameraStreamUrl;
            public RobotTarget target;
            public float deadZone;
            public int sendRateHz;
            public float cameraDistance;
            public TextSize textSize;
            public ThemeId theme;

            public SavedSettings(BarryConfig config)
            {
                backendUrl = config.backendUrl;
                cameraStreamUrl = config.cameraStreamUrl;
                target = config.target;
                deadZone = config.deadZone;
                sendRateHz = config.sendRateHz;
                cameraDistance = config.cameraDistance;
                textSize = config.textSize;
                theme = config.theme;
            }
        }

        public const string PrefsKey = "barry.config.v1";

        [Header("Connection")]
        public string backendUrl = "http://192.168.1.10:8000";
        public string cameraStreamUrl = "http://192.168.1.10:8000/api/v1/camera/stream";
        public string robotApiKey = "";
        public string cameraApiKey = "";
        public RobotTarget target = RobotTarget.Simulator;

        [Header("Control")]
        [Range(0f, 0.30f)] public float deadZone = 0.12f;
        [Range(5, 20)] public int sendRateHz = 10;

        [Header("Display")]
        [Range(1.5f, 3.0f)] public float cameraDistance = 2.0f;
        public TextSize textSize = TextSize.Medium;
        public ThemeId theme = ThemeId.Galactic;

        /// <summary>Persist settings with PlayerPrefs, excluding build-time API keys.</summary>
        public void Save() => PlayerPrefs.SetString(PrefsKey, JsonUtility.ToJson(new SavedSettings(this)));

        /// <summary>Overlay saved values (if any) onto this instance.</summary>
        public void Load()
        {
            string configuredRobotApiKey = robotApiKey;
            string configuredCameraApiKey = cameraApiKey;
            if (PlayerPrefs.HasKey(PrefsKey))
                JsonUtility.FromJsonOverwrite(PlayerPrefs.GetString(PrefsKey), this);

            try
            {
                string localConfigPath = System.IO.Path.Combine(Application.dataPath, "..", "barry_config.json");
                if (System.IO.File.Exists(localConfigPath))
                {
                    string json = System.IO.File.ReadAllText(localConfigPath);
                    JsonUtility.FromJsonOverwrite(json, this);
                }
            }
            catch { }

            robotApiKey = configuredRobotApiKey;
            cameraApiKey = configuredCameraApiKey;
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
