using System;
using System.Collections;
using Barry.Api;
using UnityEngine;

namespace Barry.Teleop
{
    public interface IRobotCommandTransport
    {
        IEnumerator SendMotion(MotionRequest request, Action<ApiResponse<CommandResult>> completed);
        IEnumerator Stop(Action<ApiResponse<CommandResult>> completed);
    }

    public sealed class BarryApiCommandTransport : IRobotCommandTransport
    {
        private readonly BarryApiClient client;

        public BarryApiCommandTransport(BarryApiClient client)
        {
            this.client = client ?? throw new ArgumentNullException(nameof(client));
        }

        public IEnumerator SendMotion(MotionRequest request, Action<ApiResponse<CommandResult>> completed) =>
            client.SendMotion(request, completed);

        public IEnumerator Stop(Action<ApiResponse<CommandResult>> completed) => client.Stop(completed);
    }

    public sealed class RobotControlService
    {
        private const int FailuresBeforeStop = 3;
        private readonly IRobotCommandTransport transport;
        private readonly Action<IEnumerator> startCoroutine;
        private float sampleAccumulator;
        private float sendRateHz;
        private bool motionInFlight;
        private bool stopInFlight;
        private bool hasSentMotion;
        private long nextRequestId;
        private long activeRequestId;
        private MotionCommand latestCommand;
        private MotionCommand sentCommand;

        public TeleopStateMachine StateMachine { get; }
        public float SendRateHz
        {
            get => sendRateHz;
            set => sendRateHz = Mathf.Clamp(value, 5f, 20f);
        }

        public MotionCommand LatestCommand => latestCommand;
        public bool MotionInFlight => motionInFlight;
        public int ConsecutiveMotionFailures { get; private set; }
        public event Action<MotionCommand> MotionSent;
        public event Action<StopReason> StopRequested;
        public event Action<StopReason, bool> StopCompleted;

        public RobotControlService(
            IRobotCommandTransport transport,
            Action<IEnumerator> startCoroutine,
            TeleopStateMachine stateMachine = null,
            float sendRateHz = 10f)
        {
            this.transport = transport ?? throw new ArgumentNullException(nameof(transport));
            this.startCoroutine = startCoroutine ?? throw new ArgumentNullException(nameof(startCoroutine));
            StateMachine = stateMachine ?? new TeleopStateMachine();
            StateMachine.StateChanged += OnStateChanged;
            SendRateHz = sendRateHz;
        }

        public void SetCommand(MotionCommand command)
        {
            latestCommand = command;
            TeleopState before = StateMachine.State;
            StateMachine.ApplyCommand(command);
            if (before == TeleopState.Driving && StateMachine.State == TeleopState.Stopped)
                RequestStop(StateMachine.LastStopReason);
        }

        public void Tick(float unscaledDeltaTime)
        {
            if (unscaledDeltaTime < 0f) throw new ArgumentOutOfRangeException(nameof(unscaledDeltaTime));
            sampleAccumulator += unscaledDeltaTime;
            float interval = 1f / SendRateHz;
            if (sampleAccumulator < interval) return;
            sampleAccumulator %= interval;

            if (motionInFlight || stopInFlight || StateMachine.State != TeleopState.Driving) return;
            if (hasSentMotion && latestCommand == sentCommand && latestCommand.IsCentered) return;
            SendLatestMotion();
        }

        public void RequestStop(StopReason reason)
        {
            StateMachine.Stop(reason);
            StopReason effectiveReason = StateMachine.LastStopReason;
            activeRequestId = ++nextRequestId;
            motionInFlight = false;
            ConsecutiveMotionFailures = 0;
            latestCommand = MotionCommand.Zero;
            hasSentMotion = false;
            StopRequested?.Invoke(effectiveReason);
            if (stopInFlight) return;
            stopInFlight = true;
            startCoroutine(transport.Stop(response =>
            {
                stopInFlight = false;
                bool delivered = response.Success && response.Value != null && response.Value.Accepted;
                StopCompleted?.Invoke(effectiveReason, delivered);
            }));
        }

        private void SendLatestMotion()
        {
            MotionCommand command = latestCommand;
            long requestId = ++nextRequestId;
            activeRequestId = requestId;
            motionInFlight = true;
            sentCommand = command;
            hasSentMotion = true;
            MotionSent?.Invoke(command);
            var request = new MotionRequest(command.Forward, command.Strafe, command.Turn);
            startCoroutine(transport.SendMotion(request, response => OnMotionCompleted(requestId, response)));
        }

        private void OnMotionCompleted(long requestId, ApiResponse<CommandResult> response)
        {
            if (requestId != activeRequestId) return;
            motionInFlight = false;
            if (response.Success && response.Value != null && response.Value.Accepted)
            {
                ConsecutiveMotionFailures = 0;
                return;
            }

            ConsecutiveMotionFailures++;
            if (ConsecutiveMotionFailures >= FailuresBeforeStop)
                RequestStop(StopReason.LinkUnreliable);
        }

        private void OnStateChanged(TeleopState previous, TeleopState next)
        {
            if (next == TeleopState.Armed) ConsecutiveMotionFailures = 0;
        }
    }
}
