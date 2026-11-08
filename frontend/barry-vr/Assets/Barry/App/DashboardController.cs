using System.Collections;
using Barry.Api;
using Barry.Teleop;
using Barry.UI;
using UnityEngine;
using UnityEngine.InputSystem;

namespace Barry.App
{
    /// <summary>
    /// Binds BarryApp state to the Dashboard views and routes controller buttons (HANDOFF §6).
    /// Control-lease calls are on hold; the dead-man grip is deliberately not implemented.
    /// </summary>
    public sealed class DashboardController : MonoBehaviour
    {
        [SerializeField] BarryApp app;
        [SerializeField] HeaderStripView header;
        [SerializeField] CameraScreenView cameraScreen;
        [SerializeField] StatusPanelView statusPanel;
        [SerializeField] ActionsPanelView actionsPanel;
        [SerializeField] ControlStripView controlStrip;

        InputAction moveAction, turnAction, stopAction, toggleActionsAction, intensityAction;
        ActionsPanelModel actionsModel;
        ActionsResponse actionsResponse;
        CameraStatusResponse cameraStatus;
        string intensity = "MEDIUM";
        float rtt = -1f, cameraPoll;
        bool intensityInFlight;

        void OnEnable()
        {
            moveAction = new InputAction("Move", InputActionType.Value, "<XRController>{LeftHand}/primary2DAxis");
            turnAction = new InputAction("Turn", InputActionType.Value, "<XRController>{RightHand}/primary2DAxis");
            stopAction = new InputAction("Stop", InputActionType.Button, "<XRController>{RightHand}/primaryButton");
            toggleActionsAction = new InputAction("Actions", InputActionType.Button, "<XRController>{RightHand}/secondaryButton");
            intensityAction = new InputAction("Intensity", InputActionType.Button, "<XRController>{LeftHand}/primaryButton");
            stopAction.performed += _ => app.RequestOperatorStop();
            toggleActionsAction.performed += _ => ToggleActions();
            intensityAction.performed += _ => CycleIntensity();
            foreach (var a in new[] { moveAction, turnAction, stopAction, toggleActionsAction, intensityAction }) a.Enable();

            controlStrip.StopClicked += app.RequestOperatorStop;
            controlStrip.IntensityChosen += SetIntensity;
            controlStrip.ExitClicked += Exit;
            actionsPanel.ActionChosen += RunAction;
            actionsPanel.ActionHovered += id => { actionsModel?.SetHovered(id); actionsPanel.Bind(actionsModel); };
        }

        void OnDisable()
        {
            foreach (var a in new[] { moveAction, turnAction, stopAction, toggleActionsAction, intensityAction }) a?.Dispose();
            if (controlStrip != null)
            {
                controlStrip.StopClicked -= app.RequestOperatorStop;
                controlStrip.IntensityChosen -= SetIntensity;
                controlStrip.ExitClicked -= Exit;
            }
        }

        void Start()
        {
            cameraScreen.Bind(app.CameraStreamSource);
            actionsModel = ActionsPanelModel.Create(null, MotionCommand.Zero, TeleopState.Disarmed, true, collapsed: true);
            actionsPanel.Bind(actionsModel);
            controlStrip.SetIntensity(intensity);
            StartCoroutine(app.ApiClient.GetActions(r => { if (r.Success) { actionsResponse = r.Value; actionsModel.UpdateActions(r.Value); actionsPanel.Bind(actionsModel); } }));
            app.TryArm(sticksCentered: true, linkAvailable: true, cameraAvailable: true);
        }

        void Update()
        {
            app.SetControllerInput(moveAction.ReadValue<Vector2>(), turnAction.ReadValue<Vector2>());

            cameraPoll -= Time.unscaledDeltaTime;
            if (cameraPoll <= 0f)
            {
                cameraPoll = 1f;
                float t0 = Time.realtimeSinceStartup;
                StartCoroutine(app.ApiClient.GetCameraStatus(r =>
                {
                    if (r.Success) { cameraStatus = r.Value; rtt = (Time.realtimeSinceStartup - t0) * 1000f; }
                }));
            }

            bool hasControl = true;   // lease on hold: Dashboard is reachable directly
            var cmd = app.LatestCommand;
            header.Bind(HeaderChipsModel.Create(app.LatestStatus, hasControl));
            statusPanel.Bind(StatusPanelModel.Create(app.LatestStatus, app.LatestTelemetry, cameraStatus, cmd, hasControl, rtt >= 0 ? rtt : (float?)null));
            cameraScreen.SetOrientation(app.LatestTelemetry);
            if (actionsModel != null) actionsModel.UpdateAvailability(cmd, app.TeleopState, hasControl);
        }

        void ToggleActions()
        {
            actionsModel.SetCollapsed(!actionsModel.IsCollapsed);
            actionsPanel.Bind(actionsModel);
        }

        void CycleIntensity()
        {
            int i = System.Array.IndexOf(ControlStripView.Levels, intensity);
            SetIntensity(ControlStripView.Levels[(i + 1) % ControlStripView.Levels.Length]);
        }

        void SetIntensity(string level)
        {
            if (intensityInFlight) return;
            intensityInFlight = true;
            StartCoroutine(app.ApiClient.SetMovementStyle(new MovementStyleRequest(level.ToLowerInvariant()), r =>
            {
                intensityInFlight = false;
                if (r.Success) { intensity = level; controlStrip.SetIntensity(level); }
            }));
        }

        void RunAction(int id)
        {
            if (!actionsModel.TryStartAction(id)) return;
            actionsPanel.Bind(actionsModel);
            StartCoroutine(app.ApiClient.RunAction(id, r =>
            {
                actionsModel.CompleteAction(id);
                actionsPanel.Bind(actionsModel);
            }));
        }

        void Exit()
        {
            app.RequestOperatorStop();
#if UNITY_EDITOR
            UnityEditor.EditorApplication.isPlaying = false;
#else
            Application.Quit();
#endif
        }
    }
}
