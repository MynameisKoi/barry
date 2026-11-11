using System;
using System.Collections;
using Barry.Api;
using UnityEngine;

namespace Barry.Teleop
{
    public interface IMissionApi
    {
        IEnumerator GetMissionStatus(Action<ApiResponse<MissionStatus>> completed);
        IEnumerator GetOperators(Action<ApiResponse<OperatorsResponse>> completed);
        IEnumerator StartMission(MissionStartRequest body, Action<ApiResponse<MissionStatus>> completed);
        IEnumerator StopMission(string reason, Action<ApiResponse<MissionStatus>> completed);
    }

    public sealed class BarryMissionApi : IMissionApi
    {
        private readonly BarryApiClient client;

        public BarryMissionApi(BarryApiClient client)
        {
            this.client = client ?? throw new ArgumentNullException(nameof(client));
        }

        public IEnumerator GetMissionStatus(Action<ApiResponse<MissionStatus>> completed) => client.GetMissionStatus(completed);
        public IEnumerator GetOperators(Action<ApiResponse<OperatorsResponse>> completed) => client.GetOperators(completed);
        public IEnumerator StartMission(MissionStartRequest body, Action<ApiResponse<MissionStatus>> completed) =>
            client.StartMission(body, completed);
        public IEnumerator StopMission(string reason, Action<ApiResponse<MissionStatus>> completed) =>
            client.StopMission(reason, completed);
    }

    /// <summary>
    /// Polls /api/mission/status (1-2 Hz, one request in flight), loads the operator list, and starts/ends
    /// missions. Missions gate MOTION only (after #89 the gateway answers MISSION_REQUIRED without one);
    /// stop and e-stop are never gated by this class. A failed status poll leaves the last known state
    /// as "unknown" rather than pretending there is or isn't a mission.
    /// </summary>
    public sealed class MissionMonitor
    {
        public const string EndReason = "Ended from the headset";

        private readonly IMissionApi api;
        private readonly Action<IEnumerator> startCoroutine;
        private float pollRateHz;
        private float accumulator;
        private bool pollInFlight;
        private bool operatorsInFlight;

        public float PollRateHz
        {
            get => pollRateHz;
            set => pollRateHz = Mathf.Clamp(value, 1f, 2f);
        }

        /// <summary>Latest known mission status; null until the first successful poll or after a failed one.</summary>
        public MissionStatus Latest { get; private set; }
        public OperatorInfo[] Operators { get; private set; }
        public string OperatorsError { get; private set; }
        public bool Starting { get; private set; }
        public string StartError { get; private set; }
        public ConfirmGate EndGate { get; } = new ConfirmGate();
        public string EndError { get; private set; }

        public bool IsActive => Latest != null && Latest.active;
        public bool StatusKnown => Latest != null;

        public event Action Changed;

        public MissionMonitor(IMissionApi api, Action<IEnumerator> startCoroutine, float pollRateHz = 1f)
        {
            this.api = api ?? throw new ArgumentNullException(nameof(api));
            this.startCoroutine = startCoroutine ?? throw new ArgumentNullException(nameof(startCoroutine));
            PollRateHz = pollRateHz;
        }

        public void Tick(float unscaledDeltaTime)
        {
            if (unscaledDeltaTime < 0f) throw new ArgumentOutOfRangeException(nameof(unscaledDeltaTime));
            EndGate.Tick(unscaledDeltaTime);
            accumulator += unscaledDeltaTime;
            float interval = 1f / PollRateHz;
            if (accumulator < interval) return;
            accumulator %= interval;
            PollNow();
        }

        public void PollNow()
        {
            if (!pollInFlight)
            {
                pollInFlight = true;
                startCoroutine(api.GetMissionStatus(OnStatus));
            }

            // Only needed while the picker is showing; retry until the list arrives.
            if (!IsActive && Operators == null && !operatorsInFlight) LoadOperators();
        }

        public void LoadOperators()
        {
            if (operatorsInFlight) return;
            operatorsInFlight = true;
            startCoroutine(api.GetOperators(response =>
            {
                operatorsInFlight = false;
                if (response.Success && response.Value?.operators != null)
                {
                    Operators = response.Value.operators;
                    OperatorsError = null;
                }
                else
                {
                    OperatorsError = string.IsNullOrWhiteSpace(response.Error) ? "Couldn't load operators." : response.Error;
                }
                Changed?.Invoke();
            }));
        }

        public bool StartMission(string operatorId, string zone)
        {
            if (Starting || IsActive || string.IsNullOrWhiteSpace(operatorId) || string.IsNullOrWhiteSpace(zone)) return false;
            Starting = true;
            StartError = null;
            Changed?.Invoke();
            startCoroutine(api.StartMission(new MissionStartRequest(operatorId, zone), response =>
            {
                Starting = false;
                if (response.Success && response.Value != null && response.Value.active)
                {
                    Latest = response.Value;
                    StartError = null;
                }
                else
                {
                    StartError = string.IsNullOrWhiteSpace(response.Error) ? "The mission couldn't be started." : response.Error;
                }
                Changed?.Invoke();
            }));
            return true;
        }

        /// <summary>Two-press end: the first press asks for confirmation; the second sends /mission/stop.</summary>
        public ConfirmStep PressEndMission()
        {
            if (!IsActive) return ConfirmStep.None;
            ConfirmStep step = EndGate.Press();
            if (step == ConfirmStep.Confirmed)
            {
                EndError = null;
                startCoroutine(api.StopMission(EndReason, response =>
                {
                    EndGate.Complete();
                    if (response.Success)
                    {
                        // The stop reply carries the final state (active = false).
                        Latest = response.Value ?? new MissionStatus { active = false };
                        Latest.active = false;
                    }
                    else
                    {
                        EndError = string.IsNullOrWhiteSpace(response.Error) ? "The mission couldn't be ended." : response.Error;
                    }
                    Changed?.Invoke();
                }));
            }
            Changed?.Invoke();
            return step;
        }

        private void OnStatus(ApiResponse<MissionStatus> response)
        {
            pollInFlight = false;
            Latest = response.Success && response.Value != null ? response.Value : null;
            Changed?.Invoke();
        }
    }
}
