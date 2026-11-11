using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace Barry.Api
{
    public readonly struct ApiResponse<T>
    {
        public readonly bool Success;
        public readonly long StatusCode;
        public readonly T Value;
        public readonly string Error;

        public ApiResponse(bool success, long statusCode, T value, string error)
        {
            Success = success;
            StatusCode = statusCode;
            Value = value;
            Error = error;
        }
    }

    public sealed class BarryApiClient
    {
        public const string ApiPrefix = "/api/v1";
        public const string AcceptedDisclaimer = "Accepted by API · not proof of movement";

        private readonly string baseUrl;
        private readonly string apiKey;
        private long sequence;

        public string SessionId { get; private set; }
        public int TimeoutSeconds { get; set; } = 2;

        public BarryApiClient(string baseUrl, string apiKey = null)
        {
            if (string.IsNullOrWhiteSpace(baseUrl)) throw new ArgumentException("A backend URL is required.", nameof(baseUrl));
            this.baseUrl = baseUrl.TrimEnd('/');
            this.apiKey = apiKey;
        }

        public IEnumerator AcquireSession(Action<ApiResponse<SessionResponse>> completed) =>
            Send("POST", ApiPrefix + "/session", null, false, completed, session =>
            {
                SessionId = session.session_id;
                sequence = 0;
            });

        public IEnumerator SendMotion(MotionRequest body, Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/motion", body, true, completed);

        public IEnumerator Stop(Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/stop", null, false, completed);

        /// <summary>
        /// Latching emergency stop. Like Stop it needs no session or sequence on the Pi, and each call is its
        /// own UnityWebRequest coroutine, so it never waits behind an in-flight motion request.
        /// </summary>
        public IEnumerator EmergencyStop(Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/emergency-stop", null, false, completed);

        /// <summary>Clears the e-stop latch. Sequenced (needs the control session). Call only after the operator confirmed.</summary>
        public IEnumerator ResetEmergency(Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/emergency-stop/reset", null, true, completed);

        public IEnumerator SetPosture(PostureRequest body, Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/posture", body, true, completed);

        /// <summary>Full robot reset (SDK reset()). Sequenced.</summary>
        public IEnumerator Reset(Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/reset", null, true, completed);

        // Mission endpoints live under /api (not /api/v1). After #89 start/stop need the same X-Robot-Key,
        // which Send() already attaches.
        public IEnumerator GetOperators(Action<ApiResponse<OperatorsResponse>> completed) =>
            Send("GET", "/api/operators", null, false, completed, null, bare => "{\"operators\":" + bare + "}");

        public IEnumerator StartMission(MissionStartRequest body, Action<ApiResponse<MissionStatus>> completed) =>
            Send("POST", "/api/mission/start", body, false, completed);

        public IEnumerator StopMission(string reason, Action<ApiResponse<MissionStatus>> completed) =>
            Send("POST", "/api/mission/stop", new MissionStopRequest(reason), false, completed);

        public IEnumerator GetMissionStatus(Action<ApiResponse<MissionStatus>> completed) =>
            Send("GET", "/api/mission/status", null, false, completed);

        // LLM brain (backend/ai/control/router.py). Same X-Robot-Key as everything else.
        public IEnumerator StartLlmTask(string goal, Action<ApiResponse<LlmStartResponse>> completed) =>
            Send("POST", ApiPrefix + "/llm/plan", new LlmGoalRequest(goal), false, completed, null, null,
                (raw, value) => LlmTaskParser.Enrich(raw, value.task));

        public IEnumerator GetLlmTask(Action<ApiResponse<LlmTaskStatus>> completed) =>
            Send("GET", ApiPrefix + "/llm/task", null, false, completed, null, null,
                (raw, value) => LlmTaskParser.Enrich(raw, value));

        public IEnumerator CancelLlmTask(Action<ApiResponse<LlmCancelResponse>> completed) =>
            Send("POST", ApiPrefix + "/llm/task/cancel", null, false, completed, null, null,
                (raw, value) => LlmTaskParser.Enrich(raw, value.task));

        public IEnumerator SetMovementStyle(MovementStyleRequest body, Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/movement-style", body, true, completed);

        public IEnumerator GetActions(Action<ApiResponse<ActionsResponse>> completed) =>
            Send("GET", ApiPrefix + "/actions", null, false, completed);

        public IEnumerator RunAction(int actionId, Action<ApiResponse<CommandResult>> completed) =>
            Send("POST", ApiPrefix + "/actions/" + actionId, null, true, completed);

        public IEnumerator GetTelemetry(Action<ApiResponse<TelemetryResponse>> completed) =>
            Send("GET", ApiPrefix + "/telemetry", null, false, completed);

        public IEnumerator GetStatus(Action<ApiResponse<StatusResponse>> completed) =>
            Send("GET", ApiPrefix + "/status", null, false, completed);

        public IEnumerator GetCameraStatus(Action<ApiResponse<CameraStatusResponse>> completed) =>
            Send("GET", ApiPrefix + "/camera/status", null, false, completed);

        private IEnumerator Send<T>(string method, string path, object body, bool sequenced,
            Action<ApiResponse<T>> completed, Action<T> onSuccess = null, Func<string, string> reshape = null,
            Action<string, T> afterParse = null) where T : class
        {
            byte[] bytes = body == null ? null : Encoding.UTF8.GetBytes(JsonUtility.ToJson(body));
            using (var request = new UnityWebRequest(baseUrl + path, method))
            {
                request.downloadHandler = new DownloadHandlerBuffer();
                request.timeout = Math.Max(1, TimeoutSeconds);
                if (bytes != null)
                {
                    request.uploadHandler = new UploadHandlerRaw(bytes);
                    request.SetRequestHeader("Content-Type", "application/json");
                }
                if (!string.IsNullOrEmpty(apiKey)) request.SetRequestHeader("X-Robot-Key", apiKey);
                if (!string.IsNullOrEmpty(SessionId)) request.SetRequestHeader("X-Control-Session", SessionId);
                if (sequenced)
                {
                    request.SetRequestHeader("X-Command-Seq", (++sequence).ToString());
                    request.SetRequestHeader("X-Command-Sent-At", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds().ToString());
                }

                yield return request.SendWebRequest();
                string text = request.downloadHandler?.text;
                if (request.result != UnityWebRequest.Result.Success)
                {
                    completed?.Invoke(new ApiResponse<T>(false, request.responseCode, null, ReadError(text, request.error)));
                    yield break;
                }

                if (reshape != null && !string.IsNullOrWhiteSpace(text)) text = reshape(text.Trim());
                if (!ApiJson.TryParse(text, out T value, out string error))
                {
                    completed?.Invoke(new ApiResponse<T>(false, request.responseCode, null, error));
                    yield break;
                }

                afterParse?.Invoke(text, value);
                onSuccess?.Invoke(value);
                completed?.Invoke(new ApiResponse<T>(true, request.responseCode, value, null));
            }
        }

        private static string ReadError(string json, string fallback)
        {
            if (ApiJson.TryParse(json, out ApiErrorBody body, out _) && !string.IsNullOrWhiteSpace(body.detail))
                return body.detail;
            return string.IsNullOrWhiteSpace(fallback) ? "Request failed." : fallback;
        }
    }
}
