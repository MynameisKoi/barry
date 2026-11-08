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
            Action<ApiResponse<T>> completed, Action<T> onSuccess = null) where T : class
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

                if (!ApiJson.TryParse(text, out T value, out string error))
                {
                    completed?.Invoke(new ApiResponse<T>(false, request.responseCode, null, error));
                    yield break;
                }

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
