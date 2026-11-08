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
        public float DeadZone { get; }
        public float SendRateHz { get; }

        private BarryAppSettings(
            string backendUrl,
            string cameraStreamUrl,
            string apiKey,
            float deadZone,
            float sendRateHz)
        {
            BackendUrl = backendUrl;
            CameraStreamUrl = cameraStreamUrl;
            ApiKey = apiKey;
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

            // Tom's current auth decision is an empty/mock key. BarryConfig is
            // deliberately non-secret, so no credential is embedded or persisted.
            const string mockApiKey = null;
            return new BarryAppSettings(
                config.backendUrl,
                config.cameraStreamUrl,
                mockApiKey,
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

        public BarryConfig Config => config;
        public BarryApiClient ApiClient { get; private set; }
        public InputMapping InputMapping { get; private set; }
        public RobotControlService RobotControl { get; private set; }
        public StatusMonitor StatusMonitor { get; private set; }
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

        private void Awake()
        {
            if (config == null)
                throw new InvalidOperationException("BarryApp requires a BarryConfig asset.");

            config.Load();
            BarryAppSettings settings = BarryAppSettings.From(config);
            ApiClient = new BarryApiClient(settings.BackendUrl, settings.ApiKey);
            InputMapping = new InputMapping(settings.DeadZone);
            RobotControl = new RobotControlService(
                new BarryApiCommandTransport(ApiClient),
                routine => StartCoroutine(routine),
                sendRateHz: settings.SendRateHz);
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
        }

        private void Start()
        {
            StatusMonitor.PollNow();
            cameraWatchdog.Reset();
            cameraStreamSource.StartStream(config.cameraStreamUrl);
        }

        private void Update()
        {
            float deltaTime = Time.unscaledDeltaTime;
            RobotControl.Tick(deltaTime);
            StatusMonitor.Tick(deltaTime);
            if (cameraWatchdog.Update(deltaTime, cameraStreamSource.FrameAgeSeconds))
                stopRouter.Route(StopReason.CameraUnavailable);
        }

        public void SetControllerInput(Vector2 leftStick, Vector2 rightStick)
        {
            RobotControl.SetCommand(InputMapping.Map(leftStick, rightStick));
        }

        public bool TryArm(bool sticksCentered, bool linkAvailable, bool cameraAvailable)
        {
            // Control leasing is intentionally on hold. Do not acquire a session;
            // the backend remains the final authority when it receives commands.
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

        private void OnStopCompleted(StopReason reason, bool delivered)
        {
            HasStopDeliveryResult = true;
            LastStopDelivered = delivered;
            StopCompleted?.Invoke(reason, delivered);
        }

        private void OnDestroy()
        {
            if (xrSessionWatcher != null && stopRouter != null)
                xrSessionWatcher.StopRequested -= stopRouter.Route;
            if (StatusMonitor != null && stopRouter != null)
                StatusMonitor.StatusPollFailed -= stopRouter.Route;
            if (RobotControl != null)
                RobotControl.StopCompleted -= OnStopCompleted;
            if (cameraStreamSource != null)
                cameraStreamSource.StopStream();
        }
    }
}
