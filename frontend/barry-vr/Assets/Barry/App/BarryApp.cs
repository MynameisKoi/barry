using System;
using Barry.Api;
using Barry.Config;
using Barry.Teleop;
using Barry.XR;
using UnityEngine;

namespace Barry.App
{
    public sealed class CameraAvailabilityWatchdog
    {
        public const float DefaultStaleFrameSeconds = 2f;
        public const float DefaultFirstFrameTimeoutSeconds = 5f;

        private readonly float staleFrameSeconds;
        private readonly float firstFrameTimeoutSeconds;
        private float startupElapsed;
        private bool hasSeenFrame;
        private bool lossReported;

        public CameraAvailabilityWatchdog(
            float staleFrameSeconds = DefaultStaleFrameSeconds,
            float firstFrameTimeoutSeconds = DefaultFirstFrameTimeoutSeconds)
        {
            if (staleFrameSeconds <= 0f)
                throw new ArgumentOutOfRangeException(nameof(staleFrameSeconds));
            if (firstFrameTimeoutSeconds <= 0f)
                throw new ArgumentOutOfRangeException(nameof(firstFrameTimeoutSeconds));
            this.staleFrameSeconds = staleFrameSeconds;
            this.firstFrameTimeoutSeconds = firstFrameTimeoutSeconds;
        }

        public bool Update(float unscaledDeltaTime, float frameAgeSeconds)
        {
            if (unscaledDeltaTime < 0f)
                throw new ArgumentOutOfRangeException(nameof(unscaledDeltaTime));

            bool hasFrame = !float.IsNaN(frameAgeSeconds) && !float.IsInfinity(frameAgeSeconds);
            if (!hasSeenFrame && !hasFrame)
            {
                startupElapsed += unscaledDeltaTime;
                return ReportOnce(startupElapsed >= firstFrameTimeoutSeconds);
            }

            if (hasFrame) hasSeenFrame = true;
            bool frameIsFresh = hasFrame && frameAgeSeconds <= staleFrameSeconds;
            if (frameIsFresh)
            {
                lossReported = false;
                return false;
            }

            return ReportOnce(hasSeenFrame && (!hasFrame || frameAgeSeconds > staleFrameSeconds));
        }

        public void Reset()
        {
            startupElapsed = 0f;
            hasSeenFrame = false;
            lossReported = false;
        }

        private bool ReportOnce(bool unavailable)
        {
            if (!unavailable || lossReported) return false;
            lossReported = true;
            return true;
        }
    }

    public readonly struct BarryAppSettings
    {
        public string BackendUrl { get; }
        public string CameraStreamUrl { get; }
        public string ApiKey { get; }
        public string CameraApiKey { get; }
        public float DeadZone { get; }
        public float SendRateHz { get; }

        private BarryAppSettings(
            string backendUrl,
            string cameraStreamUrl,
            string apiKey,
            string cameraApiKey,
            float deadZone,
            float sendRateHz)
        {
            BackendUrl = backendUrl;
            CameraStreamUrl = cameraStreamUrl;
            ApiKey = apiKey;
            CameraApiKey = cameraApiKey;
            DeadZone = deadZone;
            SendRateHz = sendRateHz;
        }

        public static BarryAppSettings From(BarryConfig config)
        {
            if (config == null) throw new ArgumentNullException(nameof(config));
            if (string.IsNullOrWhiteSpace(config.backendUrl))
                throw new ArgumentException("BarryConfig requires a backend URL.", nameof(config));
            if (string.IsNullOrWhiteSpace(config.cameraStreamUrl))
                throw new ArgumentException("BarryConfig requires a camera stream URL.", nameof(config));

            return new BarryAppSettings(
                config.backendUrl,
                config.cameraStreamUrl,
                config.robotApiKey,
                config.cameraApiKey,
                Mathf.Clamp(config.deadZone, 0f, 0.30f),
                Mathf.Clamp(config.sendRateHz, 5f, 20f));
        }
    }

    public sealed class BarryAppStopRouter
    {
        private readonly Action<StopReason> requestStop;

        public BarryAppStopRouter(Action<StopReason> requestStop)
        {
            this.requestStop = requestStop ?? throw new ArgumentNullException(nameof(requestStop));
        }

        public void Route(StopReason reason)
        {
            if (reason != StopReason.None) requestStop(reason);
        }
    }

    [DisallowMultipleComponent]
    public sealed class BarryApp : MonoBehaviour
    {
        [SerializeField] private BarryConfig config;
        [SerializeField] private XRSessionWatcher xrSessionWatcher;
        [SerializeField] private MjpegStreamSource cameraStreamSource;

        private BarryAppStopRouter stopRouter;
        private CameraAvailabilityWatchdog cameraWatchdog;
        private bool recoveryPending;

        public BarryConfig Config => config;
        public BarryApiClient ApiClient { get; private set; }
        public InputMapping InputMapping { get; private set; }
        public RobotControlService RobotControl { get; private set; }
        public StatusMonitor StatusMonitor { get; private set; }
        public EmergencyLatch EmergencyLatch { get; } = new EmergencyLatch();
        /// <summary>Mission status/operators/start/end. Missions gate motion only; stop and e-stop never wait on this.</summary>
        public MissionMonitor Mission { get; private set; }
        /// <summary>The control session the relay requires for motion/style/actions/posture/reset. Stop and e-stop never need it.</summary>
        public ControlSessionManager Session { get; private set; }
        /// <summary>The LLM brain task. Polls only while running; never gates or delays stop / e-stop.</summary>
        public LlmTaskMonitor Llm { get; private set; }
        public XRSessionWatcher XRSessionWatcher => xrSessionWatcher;
        public MjpegStreamSource CameraStreamSource => cameraStreamSource;

        public TeleopState TeleopState => RobotControl?.StateMachine.State ?? TeleopState.Disarmed;
        public StopReason LastStopReason => RobotControl?.StateMachine.LastStopReason ?? StopReason.None;
        public MotionCommand LatestCommand => RobotControl?.LatestCommand ?? MotionCommand.Zero;
        public StatusResponse LatestStatus => StatusMonitor?.LatestStatus;
        public TelemetryResponse LatestTelemetry => StatusMonitor?.LatestTelemetry;
        public bool HasStopDeliveryResult { get; private set; }
        public bool LastStopDelivered { get; private set; }

        public event Action<StopReason, bool> StopCompleted;
        /// <summary>The gateway refused motion (MISSION_REQUIRED): show "Start a mission to drive". Never retried.</summary>
        public event Action MissionRequiredHint;

        private void Awake()
        {
            if (config == null)
                throw new InvalidOperationException("BarryApp requires a BarryConfig asset.");

            config.Load();
            BarryAppSettings settings = BarryAppSettings.From(config);
            ApiClient = new BarryApiClient(settings.BackendUrl, settings.ApiKey);
            InputMapping = new InputMapping(settings.DeadZone);
            Session = new ControlSessionManager(new BarryControlSessionApi(ApiClient), routine => StartCoroutine(routine));
            ApiClient.SessionRejected += Session.OnSessionRejected;
            RobotControl = new RobotControlService(
                new BarryApiCommandTransport(ApiClient),
                routine => StartCoroutine(routine),
                sendRateHz: settings.SendRateHz,
                sessionReady: () => Session.HasSession);
            Mission = new MissionMonitor(new BarryMissionApi(ApiClient), routine => StartCoroutine(routine));
            Llm = new LlmTaskMonitor(new BarryLlmApi(ApiClient), routine => StartCoroutine(routine));
            StatusMonitor = new StatusMonitor(
                new BarryStatusApi(ApiClient),
                routine => StartCoroutine(routine));
            stopRouter = new BarryAppStopRouter(RobotControl.RequestStop);
            cameraWatchdog = new CameraAvailabilityWatchdog();

            if (xrSessionWatcher == null)
                xrSessionWatcher = GetComponent<XRSessionWatcher>() ?? gameObject.AddComponent<XRSessionWatcher>();
            if (cameraStreamSource == null)
                cameraStreamSource = GetComponent<MjpegStreamSource>() ?? gameObject.AddComponent<MjpegStreamSource>();

            xrSessionWatcher.StopRequested += stopRouter.Route;
            StatusMonitor.StatusPollFailed += stopRouter.Route;
            RobotControl.StopCompleted += OnStopCompleted;
            RobotControl.EmergencyStopCompleted += EmergencyLatch.OnEmergencyStopCompleted;
            StatusMonitor.TelemetryUpdated += EmergencyLatch.OnTelemetry;
            EmergencyLatch.ExternallyLatched += OnExternallyLatched;
            RobotControl.MissionRequired += OnMissionRequired;
            Mission.Changed += OnMissionChanged;
        }

        private void Start()
        {
            Session.Begin();   // acquire a control session; motion is held until there is one
            StatusMonitor.PollNow();
            Mission.PollNow();
            cameraWatchdog.Reset();
            cameraStreamSource.StartStream(config.cameraStreamUrl, config.cameraApiKey);
        }

        private void Update()
        {
            float deltaTime = Time.unscaledDeltaTime;
            RobotControl.Tick(deltaTime);
            StatusMonitor.Tick(deltaTime);
            EmergencyLatch.Tick(deltaTime);
            Session.Tick(deltaTime);   // recovery only: retries while there is no session, sends nothing while there is one
            Mission.Tick(deltaTime);
            Llm.Tick(deltaTime);
            TryFinishEmergencyRecovery();
            if (cameraWatchdog.Update(deltaTime, cameraStreamSource.FrameAgeSeconds))
                stopRouter.Route(StopReason.CameraUnavailable);
        }

        public void SetControllerInput(Vector2 leftStick, Vector2 rightStick)
        {
            var cmd = InputMapping.Map(leftStick, rightStick);
            if (cmd.IsCentered && !EmergencyLatch.IsLatched)
            {
                if (RobotControl.StateMachine.State == TeleopState.Stopped)
                {
                    RobotControl.StateMachine.AcknowledgeStop(sticksCentered: true);
                    RobotControl.StateMachine.TryArm(sticksCentered: true, hasControl: true, linkAvailable: true, cameraAvailable: true);
                }
                else if (RobotControl.StateMachine.State == TeleopState.Disarmed)
                {
                    RobotControl.StateMachine.TryArm(sticksCentered: true, hasControl: true, linkAvailable: true, cameraAvailable: true);
                }
            }
            RobotControl.SetCommand(cmd);
        }

        public bool TryArm(bool sticksCentered, bool linkAvailable, bool cameraAvailable)
        {
            // Arming does not wait for the control session: motion is held by RobotControlService until
            // there is one, and stop / e-stop never need it.
            bool armed = RobotControl.StateMachine.TryArm(
                sticksCentered: sticksCentered,
                hasControl: true,
                linkAvailable: linkAvailable,
                cameraAvailable: cameraAvailable);
            if (armed)
            {
                StatusMonitor.BeginNewSession();
                HasStopDeliveryResult = false;
            }
            return armed;
        }

        public void RequestOperatorStop()
        {
            RobotControl.RequestStop(StopReason.Operator);
        }

        /// <summary>Dedicated e-stop (both grips + A, or the HUD): latches at once and sends /emergency-stop.</summary>
        public void RequestEmergencyStop()
        {
            recoveryPending = false;
            EmergencyLatch.OnEmergencyStopRequested();
            RobotControl.RequestEmergencyStop();
        }

        /// <summary>
        /// The HUD reset button. First press asks for confirmation; a second press within the window sends
        /// /emergency-stop/reset. Never auto-resets.
        /// </summary>
        public void PressEmergencyReset()
        {
            if (EmergencyLatch.RequestReset() != ResetStep.SendReset) return;
            StartCoroutine(ApiClient.ResetEmergency(response =>
            {
                bool ok = response.Success && response.Value != null && response.Value.Accepted;
                EmergencyLatch.OnResetCompleted(ok, response.Error);
                recoveryPending = ok;
            }));
        }

        // After a confirmed reset the robot stays stopped. Teleop returns to Armed only when the sticks are
        // centred; the next non-zero deflection is then the required fresh deflection (HANDOFF §1.4).
        private void TryFinishEmergencyRecovery()
        {
            if (!recoveryPending || EmergencyLatch.IsLatched) return;
            if (!LatestCommand.IsCentered) return;
            RobotControl.StateMachine.AcknowledgeStop(sticksCentered: true);
            TryArm(sticksCentered: true, linkAvailable: !StatusMonitor.IsFailureLatched, cameraAvailable: true);
            if (TeleopState == TeleopState.Armed || TeleopState == TeleopState.Driving) recoveryPending = false;
        }

        private void OnMissionRequired()
        {
            MissionRequiredHint?.Invoke();
        }

        private bool missionWasActive;

        private void OnMissionChanged()
        {
            bool active = Mission.IsActive;
            if (active && LastStopReason == StopReason.MissionRequired)
                recoveryPending = true;   // re-arm only with centred sticks; the next deflection is the fresh one
            if (missionWasActive && Mission.StatusKnown && !active)
                RobotControl.RequestStop(StopReason.MissionRequired);   // the backend halted the robot; mirror it
            if (Mission.StatusKnown) missionWasActive = active;
        }

        private void OnExternallyLatched()
        {
            if (TeleopState != TeleopState.Stopped || LastStopReason != StopReason.EmergencyStop)
                RobotControl.RequestStop(StopReason.EmergencyStop);
        }

        private void OnStopCompleted(StopReason reason, bool delivered)
        {
            HasStopDeliveryResult = true;
            LastStopDelivered = delivered;
            StopCompleted?.Invoke(reason, delivered);
        }

        private void OnApplicationQuit()
        {
            // Release the lease so the web cockpit can take over at once (also stops the robot). Best effort.
            Session?.ReleaseOnQuit();
        }

        private void OnDestroy()
        {
            if (ApiClient != null && Session != null) ApiClient.SessionRejected -= Session.OnSessionRejected;
            if (xrSessionWatcher != null && stopRouter != null)
                xrSessionWatcher.StopRequested -= stopRouter.Route;
            if (StatusMonitor != null && stopRouter != null)
                StatusMonitor.StatusPollFailed -= stopRouter.Route;
            if (RobotControl != null)
            {
                RobotControl.StopCompleted -= OnStopCompleted;
                RobotControl.EmergencyStopCompleted -= EmergencyLatch.OnEmergencyStopCompleted;
            }
            if (StatusMonitor != null) StatusMonitor.TelemetryUpdated -= EmergencyLatch.OnTelemetry;
            EmergencyLatch.ExternallyLatched -= OnExternallyLatched;
            if (RobotControl != null) RobotControl.MissionRequired -= OnMissionRequired;
            if (Mission != null) Mission.Changed -= OnMissionChanged;
            if (cameraStreamSource != null)
                cameraStreamSource.StopStream();
        }
    }
}
