using System;
using System.Collections;
using Barry.Api;
using UnityEngine;

namespace Barry.Teleop
{
    public interface IStatusApi
    {
        IEnumerator GetStatus(Action<ApiResponse<StatusResponse>> completed);
        IEnumerator GetTelemetry(Action<ApiResponse<TelemetryResponse>> completed);
    }

    public sealed class BarryStatusApi : IStatusApi
    {
        private readonly BarryApiClient client;

        public BarryStatusApi(BarryApiClient client)
        {
            this.client = client ?? throw new ArgumentNullException(nameof(client));
        }

        public IEnumerator GetStatus(Action<ApiResponse<StatusResponse>> completed) =>
            client.GetStatus(completed);

        public IEnumerator GetTelemetry(Action<ApiResponse<TelemetryResponse>> completed) =>
            client.GetTelemetry(completed);
    }

    public sealed class StatusMonitor
    {
        private const int FailuresBeforeStop = 2;
        private readonly IStatusApi api;
        private readonly Action<IEnumerator> startCoroutine;
        private float pollRateHz;
        private float pollAccumulator;
        private bool statusInFlight;
        private bool telemetryInFlight;

        public float PollRateHz
        {
            get => pollRateHz;
            set => pollRateHz = Mathf.Clamp(value, 1f, 2f);
        }

        public StatusResponse LatestStatus { get; private set; }
        public TelemetryResponse LatestTelemetry { get; private set; }
        public int ConsecutiveStatusFailures { get; private set; }
        public bool IsFailureLatched { get; private set; }
        public bool StatusInFlight => statusInFlight;
        public bool TelemetryInFlight => telemetryInFlight;

        public event Action<StatusResponse> StatusUpdated;
        public event Action<TelemetryResponse> TelemetryUpdated;
        public event Action<StopReason> StatusPollFailed;

        public StatusMonitor(IStatusApi api, Action<IEnumerator> startCoroutine, float pollRateHz = 1f)
        {
            this.api = api ?? throw new ArgumentNullException(nameof(api));
            this.startCoroutine = startCoroutine ?? throw new ArgumentNullException(nameof(startCoroutine));
            PollRateHz = pollRateHz;
        }

        public void Tick(float unscaledDeltaTime)
        {
            if (unscaledDeltaTime < 0f) throw new ArgumentOutOfRangeException(nameof(unscaledDeltaTime));
            pollAccumulator += unscaledDeltaTime;
            float interval = 1f / PollRateHz;
            if (pollAccumulator < interval) return;
            pollAccumulator %= interval;
            PollNow();
        }

        public void PollNow()
        {
            if (!statusInFlight)
            {
                statusInFlight = true;
                startCoroutine(api.GetStatus(OnStatusCompleted));
            }

            if (!telemetryInFlight)
            {
                telemetryInFlight = true;
                startCoroutine(api.GetTelemetry(OnTelemetryCompleted));
            }
        }

        // The owner may call this only when beginning a newly armed session.
        // A successful poll intentionally does not clear the safety latch.
        public void BeginNewSession()
        {
            ConsecutiveStatusFailures = 0;
            IsFailureLatched = false;
        }

        private void OnStatusCompleted(ApiResponse<StatusResponse> response)
        {
            statusInFlight = false;
            if (response.Success && response.Value != null)
            {
                LatestStatus = response.Value;
                ConsecutiveStatusFailures = 0;
                StatusUpdated?.Invoke(response.Value);
                return;
            }

            ConsecutiveStatusFailures++;
            if (ConsecutiveStatusFailures < FailuresBeforeStop || IsFailureLatched) return;

            IsFailureLatched = true;
            StopReason reason = response.StatusCode <= 0
                ? StopReason.BackendUnreachable
                : StopReason.StatusUnavailable;
            StatusPollFailed?.Invoke(reason);
        }

        private void OnTelemetryCompleted(ApiResponse<TelemetryResponse> response)
        {
            telemetryInFlight = false;
            if (!response.Success || response.Value == null) return;
            LatestTelemetry = response.Value;
            TelemetryUpdated?.Invoke(response.Value);
        }
    }
}
