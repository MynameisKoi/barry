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
        [SerializeField] EmergencyBannerView emergencyBanner;
        [SerializeField] MissionPanelView missionPanel;

        InputAction moveAction, turnAction, stopAction, toggleActionsAction, intensityAction, gripLeftAction, gripRightAction;
        readonly EmergencyStopGesture emergencyGesture = new EmergencyStopGesture();
        readonly MissionPanelModel missionModel = new MissionPanelModel();
        ActionsPanelModel actionsModel;
        ActionsResponse actionsResponse;
        CameraStatusResponse cameraStatus;
        string intensity = "MEDIUM";
        float rtt = -1f, cameraPoll;
        bool intensityInFlight;

        void OnEnable()
        {
            moveAction = new InputAction("Move", InputActionType.Value);
            moveAction.AddBinding("<XRController>{LeftHand}/primary2DAxis");
            moveAction.AddBinding("<XRController>{LeftHand}/thumbstick");
            moveAction.AddBinding("<Gamepad>/leftStick");
            moveAction.AddCompositeBinding("2DVector")
                .With("Up", "<Keyboard>/w")
                .With("Down", "<Keyboard>/s")
                .With("Left", "<Keyboard>/a")
                .With("Right", "<Keyboard>/d");

            turnAction = new InputAction("Turn", InputActionType.Value);
            turnAction.AddBinding("<XRController>{RightHand}/primary2DAxis");
            turnAction.AddBinding("<XRController>{RightHand}/thumbstick");
            turnAction.AddBinding("<Gamepad>/rightStick");
            turnAction.AddCompositeBinding("2DVector")
                .With("Up", "<Keyboard>/upArrow")
                .With("Down", "<Keyboard>/downArrow")
                .With("Left", "<Keyboard>/leftArrow")
                .With("Right", "<Keyboard>/rightArrow");

            stopAction = new InputAction("Stop", InputActionType.Button);
            stopAction.AddBinding("<XRController>{RightHand}/primaryButton");
            stopAction.AddBinding("<Gamepad>/buttonEast");
            stopAction.AddBinding("<Keyboard>/space");

            toggleActionsAction = new InputAction("Actions", InputActionType.Button);
            toggleActionsAction.AddBinding("<XRController>{RightHand}/secondaryButton");
            toggleActionsAction.AddBinding("<Gamepad>/buttonNorth");
            toggleActionsAction.AddBinding("<Keyboard>/tab");

            intensityAction = new InputAction("Intensity", InputActionType.Button);
            intensityAction.AddBinding("<XRController>{LeftHand}/primaryButton");
            intensityAction.AddBinding("<Gamepad>/buttonWest");
            intensityAction.AddBinding("<Keyboard>/leftShift");

            // Dedicated e-stop = both grips + A (stopAction) held ~0.3 s. Provisional (HANDOFF §6): verify on the headset.
            gripLeftAction = new InputAction("GripLeft", InputActionType.Button);
            gripLeftAction.AddBinding("<XRController>{LeftHand}/gripPressed");
            gripLeftAction.AddBinding("<XRController>{LeftHand}/gripButton");

            gripRightAction = new InputAction("GripRight", InputActionType.Button);
            gripRightAction.AddBinding("<XRController>{RightHand}/gripPressed");
            gripRightAction.AddBinding("<XRController>{RightHand}/gripButton");
            stopAction.performed += _ => app.RequestOperatorStop();
            toggleActionsAction.performed += _ => ToggleActions();
            intensityAction.performed += _ => CycleIntensity();
            foreach (var a in new[] { moveAction, turnAction, stopAction, toggleActionsAction, intensityAction, gripLeftAction, gripRightAction }) a.Enable();
            emergencyGesture.Reset();

            controlStrip.StopClicked += app.RequestOperatorStop;
            controlStrip.IntensityChosen += SetIntensity;
            controlStrip.ExitClicked += Exit;
            if (emergencyBanner != null) emergencyBanner.ResetClicked += app.PressEmergencyReset;
            controlStrip.EndMissionClicked += OnEndMissionClicked;
            app.MissionRequiredHint += missionModel.ShowMissionRequired;
            if (missionPanel != null)
            {
                missionPanel.OperatorStep += missionModel.CycleOperator;
                missionPanel.ZoneStep += missionModel.CycleZone;
                missionPanel.StartClicked += OnStartMissionClicked;
            }
            actionsPanel.ActionChosen += RunAction;
            actionsPanel.ActionHovered += id => { actionsModel?.SetHovered(id); actionsPanel.Bind(actionsModel); };
        }

        void OnDisable()
        {
            foreach (var a in new[] { moveAction, turnAction, stopAction, toggleActionsAction, intensityAction, gripLeftAction, gripRightAction }) a?.Dispose();
            if (emergencyBanner != null && app != null) emergencyBanner.ResetClicked -= app.PressEmergencyReset;
            if (app != null) app.MissionRequiredHint -= missionModel.ShowMissionRequired;
            if (controlStrip != null) controlStrip.EndMissionClicked -= OnEndMissionClicked;
            if (missionPanel != null)
            {
                missionPanel.OperatorStep -= missionModel.CycleOperator;
                missionPanel.ZoneStep -= missionModel.CycleZone;
                missionPanel.StartClicked -= OnStartMissionClicked;
            }
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
            // The e-stop outranks everything else this frame (no other button action or stick sample runs).
            bool estop = emergencyGesture.Update(
                gripLeftAction.IsPressed(), gripRightAction.IsPressed(), stopAction.IsPressed(), Time.unscaledDeltaTime);
            if (estop) app.RequestEmergencyStop();
            else app.SetControllerInput(moveAction.ReadValue<Vector2>(), turnAction.ReadValue<Vector2>());

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
            var hud = EmergencyHudModel.From(app.EmergencyLatch);
            header.Bind(HeaderChipsModel.Create(app.LatestStatus, hasControl));
            header.SetEmergency(hud);
            header.SetMission(MissionChipModel.From(app.Mission.Latest));
            missionModel.Update(app.Mission);
            if (missionPanel != null) missionPanel.Bind(missionModel);
            controlStrip.BindEndMission(EndMissionModel.From(app.Mission));
            if (emergencyBanner != null) emergencyBanner.Bind(hud);
            statusPanel.Bind(StatusPanelModel.Create(app.LatestStatus, app.LatestTelemetry, cameraStatus, cmd, hasControl, rtt >= 0 ? rtt : (float?)null));
            cameraScreen.SetOrientation(app.LatestTelemetry);
            if (actionsModel != null) actionsModel.UpdateAvailability(cmd, app.TeleopState, hasControl);
        }

        void OnStartMissionClicked()
        {
            missionModel.ClearHint();
            OperatorInfo op = missionModel.SelectedOperator;
            if (op == null) return;
            app.Mission.StartMission(op.operator_id, missionModel.SelectedZone);
        }

        void OnEndMissionClicked() => app.Mission.PressEndMission();

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
