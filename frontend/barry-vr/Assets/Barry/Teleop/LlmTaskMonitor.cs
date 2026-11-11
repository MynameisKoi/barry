using System;
using System.Collections;
using Barry.Api;
using UnityEngine;

namespace Barry.Teleop
{
    public interface ILlmApi
    {
        IEnumerator StartLlmTask(string goal, Action<ApiResponse<LlmStartResponse>> completed);
        IEnumerator GetLlmTask(Action<ApiResponse<LlmTaskStatus>> completed);
        IEnumerator CancelLlmTask(Action<ApiResponse<LlmCancelResponse>> completed);
    }

    public sealed class BarryLlmApi : ILlmApi
    {
        private readonly BarryApiClient client;

        public BarryLlmApi(BarryApiClient client)
        {
            this.client = client ?? throw new ArgumentNullException(nameof(client));
        }

        public IEnumerator StartLlmTask(string goal, Action<ApiResponse<LlmStartResponse>> completed) => client.StartLlmTask(goal, completed);
        public IEnumerator GetLlmTask(Action<ApiResponse<LlmTaskStatus>> completed) => client.GetLlmTask(completed);
        public IEnumerator CancelLlmTask(Action<ApiResponse<LlmCancelResponse>> completed) => client.CancelLlmTask(completed);
    }

    /// <summary>
    /// Starts, watches and cancels the LLM brain task. Polls GET /llm/task at 2 Hz ONLY while a task is running.
    /// Its requests are independent coroutines, so nothing here can delay or gate STOP / e-stop, and nothing
    /// auto-starts: a task begins only from <see cref="Start"/>, which the panel calls on an operator press.
    /// </summary>
    public sealed class LlmTaskMonitor
    {
        public const float PollRateHz = 2f;

        private readonly ILlmApi api;
        private readonly Action<IEnumerator> startCoroutine;
        private float accumulator;
        private bool pollInFlight;

        public LlmTaskStatus Latest { get; private set; }
        public bool Starting { get; private set; }
        public bool Cancelling { get; private set; }
        public string Error { get; private set; }
        /// <summary>True when the operator started a task while another was running (the backend cancels the old one).</summary>
        public bool ReplacedPrevious { get; private set; }

        public bool IsRunning => Latest != null && Latest.IsRunning;

        public event Action Changed;

        public LlmTaskMonitor(ILlmApi api, Action<IEnumerator> startCoroutine)
        {
            this.api = api ?? throw new ArgumentNullException(nameof(api));
            this.startCoroutine = startCoroutine ?? throw new ArgumentNullException(nameof(startCoroutine));
        }

        public void Tick(float unscaledDeltaTime)
        {
            if (unscaledDeltaTime < 0f) throw new ArgumentOutOfRangeException(nameof(unscaledDeltaTime));
            if (!IsRunning)
            {
                accumulator = 0f;
                return;
            }

            accumulator += unscaledDeltaTime;
            float interval = 1f / PollRateHz;
            if (accumulator < interval) return;
            accumulator %= interval;
            PollNow();
        }

        public void PollNow()
        {
            if (pollInFlight) return;
            pollInFlight = true;
            startCoroutine(api.GetLlmTask(response =>
            {
                pollInFlight = false;
                if (response.Success && response.Value != null)
                {
                    Latest = response.Value;
                    Error = null;
                }
                else
                {
                    Error = string.IsNullOrWhiteSpace(response.Error) ? "Couldn't read the task." : response.Error;
                }
                Changed?.Invoke();
            }));
        }

        public bool Start(string goal)
        {
            if (Starting || string.IsNullOrWhiteSpace(goal)) return false;
            ReplacedPrevious = IsRunning;
            Starting = true;
            Error = null;
            Changed?.Invoke();
            startCoroutine(api.StartLlmTask(goal.Trim(), response =>
            {
                Starting = false;
                if (response.Success && response.Value?.task != null)
                {
                    Latest = response.Value.task;
                    Error = null;
                }
                else
                {
                    ReplacedPrevious = false;
                    Error = string.IsNullOrWhiteSpace(response.Error) ? "The task couldn't be started." : response.Error;
                }
                Changed?.Invoke();
            }));
            return true;
        }

        public bool Cancel()
        {
            if (!IsRunning || Cancelling) return false;
            Cancelling = true;
            startCoroutine(api.CancelLlmTask(response =>
            {
                Cancelling = false;
                if (response.Success && response.Value?.task != null)
                {
                    Latest = response.Value.task;
                    Error = null;
                }
                else
                {
                    Error = string.IsNullOrWhiteSpace(response.Error) ? "Couldn't cancel the task." : response.Error;
                }
                Changed?.Invoke();
            }));
            Changed?.Invoke();
            return true;
        }
    }
}
