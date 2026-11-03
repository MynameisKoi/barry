using System.Collections;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.VR
{
    public class BarryCockpitUI : MonoBehaviour
    {
        [Header("Telemetry Panel")]
        public Text coordinatesText;
        public Text headingText;
        public Text batteryText;
        public Text connectionStatusText;
        public Image batteryFillImage;

        [Header("Mission Panel")]
        public Text missionIdText;
        public Text operatorNameText;
        public Text inspectionZoneText;
        public Text missionStatusText;

        [Header("AI Alert Panel")]
        public GameObject alertBannerObject;
        public Text alertObjectText;
        public Text alertConfidenceText;
        public Text alertZoneText;
        public Text alertTimestampText;

        [Header("Emergency Stop")]
        public Button emergencyStopButton;
        public Button resetEmergencyButton;
        public GameObject emergencyStopOverlay;

        private Coroutine _alertDismissCoroutine;

        private void Start()
        {
            if (BarryNetworkManager.Instance != null)
            {
                BarryNetworkManager.Instance.OnTelemetryReceived += UpdateTelemetryUI;
                BarryNetworkManager.Instance.OnDetectionReceived += DisplayDetectionAlert;
                BarryNetworkManager.Instance.OnMissionReceived += UpdateMissionUI;
                BarryNetworkManager.Instance.OnEmergencyStopTriggered += OnEmergencyStopFired;
            }

            if (alertBannerObject != null)
            {
                alertBannerObject.SetActive(false);
            }

            if (emergencyStopOverlay != null)
            {
                emergencyStopOverlay.SetActive(false);
            }

            // Button listeners
            if (emergencyStopButton != null)
            {
                emergencyStopButton.onClick.AddListener(TriggerEmergencyStop);
            }
            if (resetEmergencyButton != null)
            {
                resetEmergencyButton.onClick.AddListener(ResetEmergencyStop);
            }
        }

        private void OnDestroy()
        {
            if (BarryNetworkManager.Instance != null)
            {
                BarryNetworkManager.Instance.OnTelemetryReceived -= UpdateTelemetryUI;
                BarryNetworkManager.Instance.OnDetectionReceived -= DisplayDetectionAlert;
                BarryNetworkManager.Instance.OnMissionReceived -= UpdateMissionUI;
                BarryNetworkManager.Instance.OnEmergencyStopTriggered -= OnEmergencyStopFired;
            }
        }

        private void UpdateTelemetryUI(RobotTelemetry telem)
        {
            if (telem == null) return;

            if (coordinatesText != null && telem.position != null)
            {
                coordinatesText.text = $"X: {telem.position.x:F2} m  |  Y: {telem.position.y:F2} m";
            }
            if (headingText != null && telem.position != null)
            {
                headingText.text = $"HEADING: {telem.position.heading:F1}°";
            }
            if (batteryText != null)
            {
                batteryText.text = $"BATTERY: {telem.battery_level:F1}%";
            }
            if (batteryFillImage != null)
            {
                batteryFillImage.fillAmount = telem.battery_level / 100f;
            }
            if (connectionStatusText != null)
            {
                connectionStatusText.text = telem.connected ? "GATEWAY: CONNECTED" : "GATEWAY: OFFLINE";
                connectionStatusText.color = telem.connected ? Color.green : Color.red;
            }

            // Emergency stop latch indicator
            if (emergencyStopOverlay != null)
            {
                emergencyStopOverlay.SetActive(telem.emergency_stopped);
            }
        }

        private void UpdateMissionUI(MissionPayload mission)
        {
            if (mission == null) return;

            if (missionIdText != null) missionIdText.text = $"MISSION: {mission.mission_id ?? "NONE"}";
            if (operatorNameText != null) operatorNameText.text = $"OPERATOR: {mission.operator_name ?? "UNAUTHORIZED"}";
            if (inspectionZoneText != null) inspectionZoneText.text = $"ZONE: {mission.zone ?? "UNASSIGNED"}";
            if (missionStatusText != null)
            {
                missionStatusText.text = $"STATUS: {(mission.status ?? "IDLE").ToUpper()}";
                missionStatusText.color = (mission.status == "in_progress") ? Color.green : Color.yellow;
            }
        }

        public void DisplayDetectionAlert(DetectionAlertPayload alert)
        {
            if (alert == null || alertBannerObject == null) return;

            alertBannerObject.SetActive(true);
            if (alertObjectText != null) alertObjectText.text = $"OBJECT: {alert.@object.ToUpper()}";
            if (alertConfidenceText != null) alertConfidenceText.text = $"CONFIDENCE: {(alert.confidence * 100f):F0}%";
            if (alertZoneText != null) alertZoneText.text = $"ZONE: {alert.zone}";
            if (alertTimestampText != null) alertTimestampText.text = alert.timestamp;

            if (_alertDismissCoroutine != null)
            {
                StopCoroutine(_alertDismissCoroutine);
            }
            _alertDismissCoroutine = StartCoroutine(DismissAlertAfterDelay(5.0f));
        }

        private IEnumerator DismissAlertAfterDelay(float delaySeconds)
        {
            yield return new WaitForSeconds(delaySeconds);
            if (alertBannerObject != null)
            {
                alertBannerObject.SetActive(false);
            }
        }

        public void TriggerEmergencyStop()
        {
            BarryNetworkManager.Instance?.TriggerEmergencyStop();
            OnEmergencyStopFired();
        }

        public void ResetEmergencyStop()
        {
            BarryNetworkManager.Instance?.ResetEmergencyStop();
            if (emergencyStopOverlay != null)
            {
                emergencyStopOverlay.SetActive(false);
            }
        }

        private void OnEmergencyStopFired()
        {
            if (emergencyStopOverlay != null)
            {
                emergencyStopOverlay.SetActive(true);
            }
        }

        // Directional controls for UI buttons
        public void OnClickForward() => BarryNetworkManager.Instance?.SendRobotCommand("forward", 0.3f);
        public void OnClickBackward() => BarryNetworkManager.Instance?.SendRobotCommand("backward", 0.3f);
        public void OnClickLeft() => BarryNetworkManager.Instance?.SendRobotCommand("left", 0.2f);
        public void OnClickRight() => BarryNetworkManager.Instance?.SendRobotCommand("right", 0.2f);
        public void OnClickStop() => BarryNetworkManager.Instance?.SendRobotCommand("stop", 0.0f);
    }
}
