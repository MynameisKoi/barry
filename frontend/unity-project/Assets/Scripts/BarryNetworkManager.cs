using System;
using System.Collections;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;

namespace Barry.VR
{
    [Serializable]
    public class RobotPosition
    {
        public float x;
        public float y;
        public float heading;
    }

    [Serializable]
    public class RobotTelemetry
    {
        public bool connected;
        public string mode;
        public float battery_level;
        public RobotPosition position;
        public float speed;
        public string last_command;
        public bool emergency_stopped;
        public string timestamp;
    }

    [Serializable]
    public class DetectionAlertPayload
    {
        public string type;
        public string @object;
        public float confidence;
        public string zone;
        public string timestamp;
        public float[] bounding_box;
    }

    [Serializable]
    public class MissionPayload
    {
        public bool active;
        public string mission_id;
        public string operator_id;
        public string operator_name;
        public string zone;
        public string status;
    }

    [Serializable]
    public class AuthEventPayload
    {
        public string type;
        public bool authorized;
        public string message;
        public string tag_uid;
    }

    public class BarryNetworkManager : MonoBehaviour
    {
        public static BarryNetworkManager Instance { get; private set; }

        [Header("Backend Gateway Configuration")]
        [Tooltip("Laptop LAN IP running FastAPI (e.g., http://192.168.1.150:8000)")]
        public string backendHttpUrl = "http://localhost:8000";

        [Tooltip("Laptop LAN WebSocket URL (e.g., ws://192.168.1.150:8000/ws)")]
        public string backendWsUrl = "ws://localhost:8000/ws";

        [Header("State")]
        public bool isWsConnected = false;
        public RobotTelemetry latestTelemetry;
        public MissionPayload latestMission;

        // Events
        public event Action<RobotTelemetry> OnTelemetryReceived;
        public event Action<DetectionAlertPayload> OnDetectionReceived;
        public event Action<AuthEventPayload> OnAuthReceived;
        public event Action<MissionPayload> OnMissionReceived;
        public event Action OnEmergencyStopTriggered;

        private ClientWebSocket _ws;
        private CancellationTokenSource _cts;

        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);
        }

        private async void Start()
        {
            await ConnectWebSocketAsync();
        }

        private void OnDestroy()
        {
            DisconnectWebSocket();
        }

        public async Task ConnectWebSocketAsync()
        {
            _cts = new CancellationTokenSource();
            _ws = new ClientWebSocket();

            try
            {
                Debug.Log($"[BARRY] Connecting to WebSocket: {backendWsUrl}");
                await _ws.ConnectAsync(new Uri(backendWsUrl), _cts.Token);
                isWsConnected = true;
                Debug.Log("[BARRY] WebSocket connected successfully.");
                _ = ReceiveLoopAsync();
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[BARRY] WebSocket connection failed: {ex.Message}. Will retry in 3 seconds.");
                isWsConnected = false;
                StartCoroutine(RetryConnectionRoutine());
            }
        }

        private IEnumerator RetryConnectionRoutine()
        {
            yield return new WaitForSeconds(3.0f);
            if (!isWsConnected)
            {
                _ = ConnectWebSocketAsync();
            }
        }

        private async Task ReceiveLoopAsync()
        {
            byte[] buffer = new byte[8192];
            while (_ws != null && _ws.State == WebSocketState.Open && !_cts.IsCancellationRequested)
            {
                try
                {
                    WebSocketReceiveResult result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Closing", CancellationToken.None);
                        isWsConnected = false;
                        break;
                    }

                    string message = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    HandleIncomingMessage(message);
                }
                catch (Exception ex)
                {
                    if (!_cts.IsCancellationRequested)
                    {
                        Debug.LogWarning($"[BARRY] WebSocket receive error: {ex.Message}");
                    }
                    isWsConnected = false;
                    break;
                }
            }

            isWsConnected = false;
            StartCoroutine(RetryConnectionRoutine());
        }

        private void HandleIncomingMessage(string json)
        {
            try
            {
                if (json.Contains("\"type\": \"telemetry\""))
                {
                    // Parse telemetry
                    int telemIndex = json.IndexOf("\"telemetry\":");
                    if (telemIndex >= 0)
                    {
                        // Substring parse for telemetry
                        string telemJson = json.Substring(telemIndex + 12).Trim();
                        // Find matching closing brace
                        int endBrace = telemJson.IndexOf("},");
                        if (endBrace > 0)
                        {
                            string rawObj = telemJson.Substring(0, endBrace + 1);
                            latestTelemetry = JsonUtility.FromJson<RobotTelemetry>(rawObj);
                            OnTelemetryReceived?.Invoke(latestTelemetry);
                        }
                    }
                }
                else if (json.Contains("\"type\": \"detection\""))
                {
                    var alert = JsonUtility.FromJson<DetectionAlertPayload>(json);
                    OnDetectionReceived?.Invoke(alert);
                }
                else if (json.Contains("\"type\": \"auth_event\""))
                {
                    var auth = JsonUtility.FromJson<AuthEventPayload>(json);
                    OnAuthReceived?.Invoke(auth);
                }
                else if (json.Contains("\"type\": \"emergency_stop\""))
                {
                    OnEmergencyStopTriggered?.Invoke();
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[BARRY] Error parsing WebSocket message: {e.Message}");
            }
        }

        public void DisconnectWebSocket()
        {
            _cts?.Cancel();
            if (_ws != null)
            {
                try { _ws.Dispose(); } catch { }
                _ws = null;
            }
            isWsConnected = false;
        }

        // ==========================================
        // REST Command Methods
        // ==========================================

        public void SendRobotCommand(string action, float speed = 0.2f)
        {
            string payload = $"{{\"action\":\"{action}\",\"speed\":{speed.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture)}}}";
            StartCoroutine(PostRequestRoutine("/api/robot/command", payload));
        }

        public void TriggerEmergencyStop()
        {
            SendRobotCommand("emergency_stop", 0.0f);
        }

        public void ResetEmergencyStop()
        {
            StartCoroutine(PostRequestRoutine("/api/robot/reset-emergency", "{}"));
        }

        public void StartMission(string operatorId, string zone)
        {
            string payload = $"{{\"operator_id\":\"{operatorId}\",\"zone\":\"{zone}\"}}";
            StartCoroutine(PostRequestRoutine("/api/mission/start", payload, (res) => {
                latestMission = JsonUtility.FromJson<MissionPayload>(res);
                OnMissionReceived?.Invoke(latestMission);
            }));
        }

        public void StopMission(string reason = "Inspection Complete")
        {
            string payload = $"{{\"reason\":\"{reason}\"}}";
            StartCoroutine(PostRequestRoutine("/api/mission/stop", payload, (res) => {
                latestMission = JsonUtility.FromJson<MissionPayload>(res);
                OnMissionReceived?.Invoke(latestMission);
            }));
        }

        private IEnumerator PostRequestRoutine(string path, string jsonPayload, Action<string> onSuccess = null)
        {
            string url = $"{backendHttpUrl.TrimEnd('/')}{path}";
            using (UnityWebRequest req = new UnityWebRequest(url, "POST"))
            {
                byte[] bodyRaw = Encoding.UTF8.GetBytes(jsonPayload);
                req.uploadHandler = new UploadHandlerRaw(bodyRaw);
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", "application/json");

                yield return req.SendWebRequest();

                if (req.result == UnityWebRequest.Result.Success)
                {
                    onSuccess?.Invoke(req.downloadHandler.text);
                }
                else
                {
                    Debug.LogWarning($"[BARRY] HTTP POST error {url}: {req.error}");
                }
            }
        }
    }
}
