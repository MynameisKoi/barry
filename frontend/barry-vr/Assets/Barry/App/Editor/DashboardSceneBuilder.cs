using Barry.Config;
using Barry.Environment;
using Barry.Theme;
using Barry.UI;
using Barry.UI.Styling;
using Barry.XR;
using Barry.XR.Tooltips;
using TMPro;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.XR;
using UnityEngine.UI;
using UnityEngine.XR.Interaction.Toolkit.UI;

namespace Barry.App.Editor
{
    /// <summary>
    /// Menu: Barry/Build Dashboard Scene. Regenerates Scenes/Dashboard.unity and UI/Prefabs/*.prefab.
    ///
    /// Layout follows design-source/Main.dc.html. Each canvas is laid out in mockup px and scaled uniformly by
    /// S = (HANDOFF §4 canvas width) / (mockup panel width), so proportions match the mockup while real sizes
    /// stay per §4. Font sizes never go below the §4 cap-height rule (1.2 cm per metre of viewing distance).
    /// </summary>
    public static class DashboardSceneBuilder
    {
        const string Root = "Assets/Barry";
        const float Eye = 1.6f;
        // HANDOFF §4 says 30°, but flat panels 0.45 m wide at 1.3 m span ±9.8° while the 1.8 m camera
        // screen/header at 2.0 m span ±24.2°. Clearing them from the head position needs >= 34°, so use 36°.
        const float SidePanelYaw = 36f;

        static TMP_FontAsset body, mono, brand;
        static ThemeTokens look;   // Galactic tokens: build-time colours so the scene reads correctly in Edit mode
        static float S = 1f;       // mm per mockup px for the canvas being built
        static float Dist = 1f;    // viewing distance (m) of the canvas being built

        static readonly Color Grey3D = H("#3D3D3A"), Grey6B = H("#6B6A65"), Track = H("#EFECE6"), Divider = H("#E6E2DA"),
            RowFill = H("#F7F5F0"), RunBorder = H("#CFCAC0"), ExitBorder = H("#9C978D"), Reticle = H("#4A4F66"),
            CamTitle = H("#B9BDCF"), CamSub = H("#9EA3B8"), ChipWhite = new Color(1f, 1f, 1f, 0.92f);

        [MenuItem("Barry/Build Dashboard Scene")]
        public static void Build()
        {
            if (EditorApplication.isPlaying || EditorApplication.isPlayingOrWillChangePlaymode)
            {
                Debug.LogWarning("Barry: exit Play mode before running 'Build Dashboard Scene' (scene creation is not allowed in Play mode).");
                return;
            }
            // NewScene(Single) unloads unused assets, so load assets only after it;
            // references loaded earlier serialize as null.
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            body = Load<TMP_FontAsset>("Theme/Fonts/Inter SDF.asset");
            mono = Load<TMP_FontAsset>("Theme/Fonts/JetBrainsMono SDF.asset");
            brand = Load<TMP_FontAsset>("Theme/Fonts/Orbitron SDF.asset");
            var config = Load<BarryConfig>("Config/BarryConfig.asset");
            var galactic = Load<ThemeTokens>("Theme/Tokens/Galactic.asset");
            var light = Load<ThemeTokens>("Theme/Tokens/Light.asset");
            look = galactic;

            // Rig: camera at eye height, headset-tracked; the Galactic environment supplies the skybox.
            var camGo = new GameObject("Main Camera") { tag = "MainCamera" };
            camGo.transform.position = new Vector3(0, Eye, 0);
            var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.Skybox;
            cam.backgroundColor = galactic.envSky;   // fallback if the skybox is missing
            cam.nearClipPlane = 0.1f;
            camGo.AddComponent<AudioListener>();
            var tpd = camGo.AddComponent<TrackedPoseDriver>();
            tpd.positionInput = new InputActionProperty(new InputAction("Position", binding: "<XRHMD>/centerEyePosition"));
            tpd.rotationInput = new InputActionProperty(new InputAction("Rotation", binding: "<XRHMD>/centerEyeRotation"));

            EnvironmentView.Create(null, galactic);

            // Tracked controllers (poses from the XR device; Edit-mode defaults put them in view).
            var rig = new GameObject("XR Rig");
            var leftCtrl = NewController(rig.transform, "Left Controller", "LeftHand", new Vector3(-0.25f, Eye - 0.35f, 0.35f));
            var rightCtrl = NewController(rig.transform, "Right Controller", "RightHand", new Vector3(0.25f, Eye - 0.35f, 0.35f));
            ControllerTooltips.Create(leftCtrl, rightCtrl, galactic);

            var es = new GameObject("EventSystem");
            es.AddComponent<EventSystem>();
            es.AddComponent<XRUIInputModule>();

            // Panels (HANDOFF §4 placement; canvas scale 0.001).
            var header = BuildHeader();
            Place(header.gameObject, new Vector3(0, Eye + 0.588f, 2.0f), Quaternion.identity);
            var camera = BuildCameraScreen();
            Place(camera.gameObject, new Vector3(0, Eye, 2.0f), Quaternion.identity);
            var status = BuildStatus();
            PlaceOnArc(status.gameObject, -SidePanelYaw, 1.3f, Eye);
            var actions = BuildActions();
            PlaceOnArc(actions.gameObject, SidePanelYaw, 1.3f, Eye);
            var strip = BuildControlStrip();
            float below = 25f * Mathf.Deg2Rad;
            Place(strip.gameObject, new Vector3(0, Eye - 0.9f * Mathf.Sin(below), 0.9f * Mathf.Cos(below)), Quaternion.Euler(15f, 0, 0));

            // App + controller.
            var appGo = new GameObject("BarryApp");
            appGo.SetActive(false);   // so serialized refs are set before Awake
            var app = appGo.AddComponent<BarryApp>();
            appGo.AddComponent<XRSessionWatcher>();
            SetRef(app, "config", config);
            var ctrl = appGo.AddComponent<DashboardController>();
            SetRef(ctrl, "app", app);
            SetRef(ctrl, "header", header.GetComponentInChildren<HeaderStripView>(true));
            SetRef(ctrl, "cameraScreen", camera.GetComponentInChildren<CameraScreenView>(true));
            SetRef(ctrl, "statusPanel", status.GetComponentInChildren<StatusPanelView>(true));
            SetRef(ctrl, "actionsPanel", actions.GetComponentInChildren<ActionsPanelView>(true));
            SetRef(ctrl, "controlStrip", strip.GetComponentInChildren<ControlStripView>(true));

            var themeGo = new GameObject("ThemeApplier");
            var ta = themeGo.AddComponent<ThemeApplier>();
            SetRef(ta, "galactic", galactic);
            SetRef(ta, "light", light);
            SetRef(ta, "config", config);
            themeGo.transform.SetSiblingIndex(0);
            appGo.SetActive(true);

            // Save prefabs (panels), then the scene.
            string pdir = Root + "/UI/Prefabs";
            foreach (var g in new[] { header, camera, status, actions, strip })
                PrefabUtility.SaveAsPrefabAssetAndConnect(g.gameObject, $"{pdir}/{g.name}.prefab", InteractionMode.AutomatedAction);

            PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed;   // the only Player setting changed
            EditorSceneManager.SaveScene(scene, Root + "/Scenes/Dashboard.unity");
            AssetDatabase.SaveAssets();
            Debug.Log("Barry: Dashboard scene built.");
        }

        static Transform NewController(Transform parent, string name, string hand, Vector3 editModePosition)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            go.transform.position = editModePosition;
            var pose = go.AddComponent<TrackedPoseDriver>();
            pose.positionInput = new InputActionProperty(new InputAction("Position", binding: $"<XRController>{{{hand}}}/devicePosition"));
            pose.rotationInput = new InputActionProperty(new InputAction("Rotation", binding: $"<XRController>{{{hand}}}/deviceRotation"));
            return go.transform;
        }

        // ---------------------------------------------------------------- panels (mockup px, scaled by S)

        static Canvas BuildHeader()
        {
            Begin(1800f / 880f, 2.0f);
            var c = NewCanvas("HeaderStrip", 1800, M(56));
            PanelRoot(c, 14, true, RoundedPanel.DefaultGlowStrength);
            var view = c.gameObject.AddComponent<HeaderStripView>();

            var left = Row(c.transform, "Left", 16, TextAnchor.MiddleLeft, leftEdge: true, inset: 20);
            Txt(left, "Wordmark", "BARRY", 20, ThemeRole.Brand, FontRole.Brand, bold: true, tracking: 0.18f);
            var mode = Pill(left, "ModePill", 32, 12, 8, 8, ThemeRole.NeutralFill, ThemeRole.NeutralBorder, 1f);
            var modeText = Txt(mode.transform, "Mode", "MODE · MANUAL", 13, Grey3D, FontRole.Mono, bold: true);
            Txt(mode.transform, "ModeMore", "· more modes later", 13, ThemeRole.TextSecondary, FontRole.Mono);

            var right = Row(c.transform, "Right", 10, TextAnchor.MiddleRight, leftEdge: false, inset: 20);
            var robot = Chip(right, "RobotChip", 32, 12, 8, 8, 13, overlay: false);
            var control = Chip(right, "ControlChip", 32, 12, 8, 8, 13, overlay: false);

            SetRef(view, "modeLabel", modeText);
            SetRef(view, "robotChip", robot);
            SetRef(view, "controlChip", control);
            Relayout(c);
            return c;
        }

        static Canvas BuildCameraScreen()
        {
            Begin(1800f / 880f, 2.0f);
            var c = NewCanvas("CameraScreen", 1800, M(495));
            PanelRoot(c, 16, true, RoundedPanel.CameraGlowStrength);
            var view = c.gameObject.AddComponent<CameraScreenView>();

            var inner = Box(c.transform, "Inner", 0, 0, 0, 0, stretch: M(10));
            Skin(inner, M(8), look.videoBg, Color.clear, 0f, fillRole: ThemeRole.VideoBg);

            var vid = new GameObject("Video", typeof(RectTransform), typeof(RawImage)).GetComponent<RawImage>();
            vid.transform.SetParent(c.transform, false);
            Stretch(vid.rectTransform, M(10));
            vid.color = new Color(1, 1, 1, 0);   // invisible until the first frame
            vid.raycastTarget = false;

            // Placeholder while there is no frame: reticle + copy from the mockup.
            var ph = new GameObject("Placeholder", typeof(RectTransform));
            ph.transform.SetParent(inner.transform, false);
            Stretch((RectTransform)ph.transform, 0);
            var ring = Box(ph.transform, "ReticleRing", 0, 0, 44, 44, centre: true);
            Skin(ring, M(22), new Color(Reticle.r, Reticle.g, Reticle.b, 0.004f), Reticle, M(2f));   // alpha 0 would be culled
            float[] tx = { 0, 0, -45, 45 }, ty = { 45, -45, 0, 0 }, tw = { 2, 2, 30, 30 }, th = { 30, 30, 2, 2 };
            for (int i = 0; i < 4; i++)
            {
                var tick = Box(ph.transform, "Tick" + i, tx[i], -ty[i], tw[i], th[i], centre: true);
                tick.color = Reticle;
            }
            var title = Txt(ph.transform, "Title", "[ LIVE ROBOT CAMERA ]", 15, CamTitle, FontRole.Mono, bold: true, tracking: 0.12f, align: TextAlignmentOptions.Center);
            PlaceTopCentre(title.rectTransform, 300, 24, 700);
            var sub = Txt(ph.transform, "Sub", "MJPEG stream · frame scales to stream aspect", 13, CamSub, FontRole.Mono, align: TextAlignmentOptions.Center);
            PlaceTopCentre(sub.rectTransform, 332, 20, 780);

            // Overlay chips (white @ 92 %, r6): left group and right roll/pitch.
            var left = Row(inner.transform, "ChipsLeft", 8, TextAnchor.UpperLeft, leftEdge: true, inset: 14, top: 14, fitVertical: true);
            var live = Chip(left, "LiveChip", 26, 10, 6, 6, 13, overlay: true);
            var fps = OverlayChip(left, "FpsChip", "-- fps");
            var age = OverlayChip(left, "AgeChip", "frame age -- ms");
            var rightRow = Row(inner.transform, "ChipsRight", 8, TextAnchor.UpperRight, leftEdge: false, inset: 14, top: 14, fitVertical: true);
            var rp = OverlayChip(rightRow, "RollPitchChip", "ROLL --° · PITCH --°");

            SetRef(view, "video", vid);
            SetRef(view, "screenRect", c.GetComponent<RectTransform>());
            SetRef(view, "bezelMm", null, M(10));
            SetRef(view, "fpsLabel", fps); SetRef(view, "ageLabel", age); SetRef(view, "rollPitchLabel", rp);
            SetRef(view, "placeholder", ph); SetRef(view, "liveChip", live);
            Relayout(c);
            return c;
        }

        static Canvas BuildStatus()
        {
            Begin(450f / 270f, 1.3f);
            var c = NewCanvas("StatusPanel", 450, M(470));
            PanelRoot(c, 16, true, RoundedPanel.DefaultGlowStrength);
            var view = c.gameObject.AddComponent<StatusPanelView>();

            Txt(c.transform, "Title", "STATUS", 13, ThemeRole.TextSecondary, FontRole.Body, bold: true, tracking: 0.14f, rect: P(20, 20, 200, 16));
            var cellNames = new[] { "Robot", "Control", "Camera", "Backend RTT" };
            float[] cx = { 20, 140, 20, 140 }, cy = { 50, 50, 98, 98 };
            bool[] mon = { false, false, true, true };
            var vals = new TMP_Text[4];
            for (int i = 0; i < 4; i++)
            {
                Txt(c.transform, cellNames[i] + "L", cellNames[i], 12, ThemeRole.TextSecondary, FontRole.Body, rect: P(cx[i], cy[i], 110, 15));
                vals[i] = Value(c.transform, cellNames[i] + "V", "--", 16, mon[i] ? FontRole.Mono : FontRole.Body, P(cx[i], cy[i] + 17, 110, 19));
            }
            Txt(c.transform, "BatteryL", "Battery", 12, ThemeRole.TextSecondary, FontRole.Body, rect: P(20, 148, 100, 17));
            var bat = Value(c.transform, "BatteryV", "-- %", 14, FontRole.Mono, P(130, 148, 120, 17));
            bat.alignment = TextAlignmentOptions.MidlineRight;
            var track = Box(c.transform, "BatteryTrack", 20, 171, 230, 10);
            Skin(track, M(5), Track, look.neutralBorder, M(1f), borderRole: ThemeRole.NeutralBorder);
            var bfill = Box(track.transform, "BatteryFill", 0, 0, 0, 0, stretch: 0);
            var bfillSkin = Skin(bfill, M(5), look.accent, Color.clear, 0f, fillRole: ThemeRole.Accent);
            bfill.gameObject.SetActive(false);   // unknown battery = empty track
            var div = Box(c.transform, "Divider", 20, 195, 230, 1); div.color = Divider;
            Txt(c.transform, "CmdTitle", "COMMAND SENT", 12, ThemeRole.TextSecondary, FontRole.Body, bold: true, tracking: 0.12f, rect: P(20, 210, 230, 15));

            string[] names = { "Forward", "Strafe", "Turn" };
            var rows = new StatusPanelView.BarRow[3];
            for (int i = 0; i < 3; i++)
            {
                float y = 239 + i * 26;
                Txt(c.transform, names[i] + "L", names[i], 13, ThemeRole.TextSecondary, FontRole.Mono, rect: P(20, y, 62, 16));
                var tr = Box(c.transform, names[i] + "Track", 88, y + 4, 104, 8);
                Skin(tr, M(4), Track, Color.clear, 0f);
                var f = Box(tr.transform, names[i] + "Fill", 0, 0, 0, 0, stretch: 0);
                Skin(f, M(4), look.accent, Color.clear, 0f, fillRole: ThemeRole.Accent);
                f.rectTransform.anchorMin = new Vector2(0.5f, 0); f.rectTransform.anchorMax = new Vector2(0.5f, 1);
                f.rectTransform.offsetMin = f.rectTransform.offsetMax = Vector2.zero;
                f.gameObject.SetActive(false);   // value 0 = no fill, only the centre tick
                var tick = Box(tr.transform, "CentreTick", 0, 0, 2, 12, centre: true); tick.color = ExitBorder;
                var v = Value(c.transform, names[i] + "V", "0.00", 13, FontRole.Mono, P(202, y, 48, 16));
                v.alignment = TextAlignmentOptions.MidlineRight;
                rows[i] = new StatusPanelView.BarRow { value = v, fill = f.rectTransform, centreTick = tick.gameObject };
            }

            var chip = Box(c.transform, "FooterChip", 20, 402, 230, 48);
            Skin(chip, M(8), look.neutralFill, Color.clear, 0f, fillRole: ThemeRole.NeutralFill);
            var cdot = Box(chip.transform, "Dot", 10, 20, 8, 8);
            Skin(cdot, M(4), look.accent, Color.clear, 0f, fillRole: ThemeRole.Accent);
            var foot = Txt(chip.transform, "Footer", StatusPanelModel.Footer, 12, Grey3D, FontRole.Body, rect: P(26, 0, 194, 48));
            foot.textWrappingMode = TextWrappingModes.Normal;

            SetRef(view, "robot", vals[0]); SetRef(view, "control", vals[1]); SetRef(view, "cameraFps", vals[2]); SetRef(view, "backendRtt", vals[3]);
            SetRef(view, "battery", bat); SetRef(view, "batteryFill", bfill.rectTransform); SetRef(view, "batteryFillSkin", bfillSkin); SetRef(view, "footer", foot);
            var so = new SerializedObject(view);
            string[] f2 = { "forward", "strafe", "turn" };
            for (int i = 0; i < 3; i++)
            {
                var p = so.FindProperty(f2[i]);
                p.FindPropertyRelative("value").objectReferenceValue = rows[i].value;
                p.FindPropertyRelative("fill").objectReferenceValue = rows[i].fill;
                p.FindPropertyRelative("centreTick").objectReferenceValue = rows[i].centreTick;
            }
            so.ApplyModifiedPropertiesWithoutUndo();
            Relayout(c);
            return c;
        }

        static Canvas BuildActions()
        {
            Begin(450f / 270f, 1.3f);
            var c = NewCanvas("ActionsPanel", 450, M(470));
            var view = c.gameObject.AddComponent<ActionsPanelView>();

            var expanded = new GameObject("Expanded", typeof(RectTransform));
            expanded.transform.SetParent(c.transform, false);
            Stretch((RectTransform)expanded.transform, 0);
            var frame = Box(expanded.transform, "Frame", 0, 0, 0, 0, stretch: 0);
            Skin(frame, M(16), look.panelBg, look.panelBorder, M(1f), true, RoundedPanel.DefaultGlowStrength, ThemeRole.PanelBg, ThemeRole.PanelBorder);
            Txt(expanded.transform, "Title", "ACTIONS", 13, ThemeRole.TextSecondary, FontRole.Body, bold: true, tracking: 0.14f, rect: P(20, 20, 150, 16));
            var close = Txt(expanded.transform, "Close", "B · close", 12, ThemeRole.TextSecondary, FontRole.Mono, rect: P(130, 20, 120, 16));
            close.alignment = TextAlignmentOptions.MidlineRight;

            var list = new GameObject("List", typeof(RectTransform), typeof(VerticalLayoutGroup));
            list.transform.SetParent(expanded.transform, false);
            Place((RectTransform)list.transform, P(20, 48, 230, 352));
            var vlg = list.GetComponent<VerticalLayoutGroup>();
            vlg.spacing = M(10); vlg.childControlWidth = true; vlg.childControlHeight = false;
            vlg.childForceExpandWidth = true; vlg.childForceExpandHeight = false; vlg.childAlignment = TextAnchor.UpperLeft;

            var tmplImg = Box(list.transform, "ItemTemplate", 0, 0, 230, 52);
            Skin(tmplImg, M(10), RowFill, look.neutralBorder, M(1f));
            tmplImg.raycastTarget = true;
            tmplImg.gameObject.AddComponent<LayoutElement>().preferredHeight = M(52);
            var tmpl = tmplImg.gameObject.AddComponent<Button>();
            tmpl.targetGraphic = tmplImg;
            var tl = Txt(tmplImg.transform, "Text", "[Action]", 15, ThemeRole.TextPrimary, FontRole.Body, bold: true);
            Stretch(tl.rectTransform, 0); tl.rectTransform.offsetMin = new Vector2(M(14), 0);
            tl.alignment = TextAlignmentOptions.MidlineLeft;
            tmpl.gameObject.SetActive(false);

            var empty = Txt(expanded.transform, "Empty", "No actions available", 15, Grey6B, FontRole.Body, rect: P(20, 62, 230, 24));
            empty.gameObject.SetActive(false);   // shown by the view when the list is empty
            var foot = Txt(expanded.transform, "Footer", "", 13, ThemeRole.TextSecondary, FontRole.Body, rect: P(20, 414, 230, 36));
            foot.textWrappingMode = TextWrappingModes.Normal;
            foot.alignment = TextAlignmentOptions.BottomLeft;

            // Collapsed: small 140x48 pill, centred where the panel was.
            var tab = Box(c.transform, "CollapsedTab", 0, 0, 140, 48, centre: true);
            Skin(tab, M(12), look.panelBg, look.panelBorder, M(1f), true, RoundedPanel.DefaultGlowStrength, ThemeRole.PanelBg, ThemeRole.PanelBorder);
            var tlabel = Txt(tab.transform, "Label", "ACTIONS · B", 13, Grey3D, FontRole.Mono, bold: true, align: TextAlignmentOptions.Center);
            Stretch(tlabel.rectTransform, 0);
            expanded.SetActive(false);   // starts closed: only the 'ACTIONS · B' pill shows (B opens it)

            SetRef(view, "emptyLabel", empty);
            SetRef(view, "expanded", expanded); SetRef(view, "collapsedTab", tab.gameObject); SetRef(view, "collapsedLabel", tlabel);
            SetRef(view, "listRoot", (RectTransform)list.transform); SetRef(view, "itemTemplate", tmpl); SetRef(view, "footer", foot);
            SetRef(view, "mmPerMockupPx", null, S);
            Relayout(c);
            return c;
        }

        static Canvas BuildControlStrip()
        {
            Begin(900f / 800f, 0.9f);
            var c = NewCanvas("ControlStrip", 900, M(108));
            PanelRoot(c, 16, true, RoundedPanel.DefaultGlowStrength);
            var view = c.gameObject.AddComponent<ControlStripView>();

            Txt(c.transform, "IntensityL", "INTENSITY", 12, ThemeRole.TextSecondary, FontRole.Body, bold: true, tracking: 0.14f, rect: P(24, 16.5f, 200, 15));
            var lv = new Button[3];
            for (int i = 0; i < 3; i++)
            {
                bool selected = ControlStripView.Levels[i] == "MEDIUM";
                var img = Box(c.transform, "Level" + ControlStripView.Levels[i], 24 + i * 102, 39.5f, 96, 52);
                if (selected) Skin(img, M(10), look.accent, look.accent, M(1f));
                else Skin(img, M(10), look.neutralFill, look.neutralBorder, M(1f));
                img.raycastTarget = true;
                lv[i] = img.gameObject.AddComponent<Button>(); lv[i].targetGraphic = img;
                var t = Txt(img.transform, "Text", ControlStripView.Levels[i], 14, selected ? Color.white : Grey3D, FontRole.Body, bold: true, tracking: 0.06f, align: TextAlignmentOptions.Center);
                Stretch(t.rectTransform, 0);
            }

            var stopImg = Box(c.transform, "Stop", 360, 14, 240, 80);
            Skin(stopImg, M(14), look.dangerSolid, Color.clear, 0f);
            stopImg.raycastTarget = true;
            var stop = stopImg.gameObject.AddComponent<Button>(); stop.targetGraphic = stopImg;
            var st = Txt(stopImg.transform, "Text", "STOP", 30, Color.white, FontRole.Body, bold: true, tracking: 0.12f, align: TextAlignmentOptions.Center, rect: P(0, 8, 240, 40));
            var hint = Txt(stopImg.transform, "Hint", "A button · or point + trigger", 12, Color.white, FontRole.Mono, align: TextAlignmentOptions.Center, rect: P(0, 50, 240, 20));

            var exitImg = Box(c.transform, "Exit", 636, 28, 140, 52);
            Skin(exitImg, M(10), Color.white, ExitBorder, M(1f));
            exitImg.raycastTarget = true;
            var exit = exitImg.gameObject.AddComponent<Button>(); exit.targetGraphic = exitImg;
            var et = Txt(exitImg.transform, "Text", "Exit VR", 15, ThemeRole.TextPrimary, FontRole.Body, bold: true, align: TextAlignmentOptions.Center);
            Stretch(et.rectTransform, 0);

            var so = new SerializedObject(view);
            var arr = so.FindProperty("levelButtons");
            arr.arraySize = 3;
            for (int i = 0; i < 3; i++) arr.GetArrayElementAtIndex(i).objectReferenceValue = lv[i];
            so.FindProperty("stopButton").objectReferenceValue = stop;
            so.FindProperty("exitButton").objectReferenceValue = exit;
            so.FindProperty("stopHint").objectReferenceValue = hint;
            so.ApplyModifiedPropertiesWithoutUndo();
            Relayout(c);
            return c;
        }

        // ---------------------------------------------------------------- scaling helpers

        static Color H(string hex) { ColorUtility.TryParseHtmlString(hex, out var c); return c; }
        static void Begin(float mmPerPx, float distanceM) { S = mmPerPx; Dist = distanceM; }
        static float M(float px) => px * S;
        /// <summary>Font size in canvas mm: mockup size scaled, but never below the §4 cap-height rule.</summary>
        static float Fs(float px) => Mathf.Max(px * S, 12f * Dist / 0.73f);

        // rect in mockup px: x, y (down), w, h
        static Vector4 P(float x, float y, float w, float h) => new Vector4(x, y, w, h);

        static void Place(RectTransform rt, Vector4 r)
        {
            rt.anchorMin = rt.anchorMax = new Vector2(0, 1);
            rt.pivot = new Vector2(0, 1);
            rt.anchoredPosition = new Vector2(M(r.x), -M(r.y));
            rt.sizeDelta = new Vector2(M(r.z), M(r.w));
        }

        static void PlaceTopCentre(RectTransform rt, float yPx, float hPx, float wPx)
        {
            rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 1);
            rt.pivot = new Vector2(0.5f, 1);
            rt.anchoredPosition = new Vector2(0, -M(yPx));
            rt.sizeDelta = new Vector2(M(wPx), M(hPx));
        }

        static void Stretch(RectTransform rt, float inset)
        {
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = new Vector2(inset, inset); rt.offsetMax = new Vector2(-inset, -inset);
        }

        // ---------------------------------------------------------------- element helpers

        static Canvas NewCanvas(string name, float wMm, float hMm)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Canvas), typeof(TrackedDeviceGraphicRaycaster));
            var canvas = go.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            var rt = go.GetComponent<RectTransform>();
            rt.sizeDelta = new Vector2(wMm, hMm);
            rt.localScale = Vector3.one * 0.001f;
            return canvas;
        }

        static void Place(GameObject go, Vector3 pos, Quaternion rot) { go.transform.SetPositionAndRotation(pos, rot); }

        static void PlaceOnArc(GameObject go, float yawDeg, float dist, float y)
        {
            float a = yawDeg * Mathf.Deg2Rad;
            go.transform.SetPositionAndRotation(new Vector3(Mathf.Sin(a) * dist, y, Mathf.Cos(a) * dist), Quaternion.Euler(0, yawDeg, 0));
        }

        /// <summary>Image with a RoundedPanel background and a PanelSkin that holds its colours.</summary>
        static PanelSkin Skin(Image img, float radiusMm, Color fill, Color border, float borderMm,
            bool glow = false, float glowStrength = 0f, ThemeRole? fillRole = null, ThemeRole? borderRole = null)
        {
            RoundedPanel.Apply(img, radiusMm, glow, glowStrength);
            var skin = img.GetComponent<PanelSkin>();
            if (skin == null) skin = img.gameObject.AddComponent<PanelSkin>();
            skin.Configure(fill, border, borderMm, fillRole, borderRole);
            return skin;
        }

        static void PanelRoot(Canvas c, float radiusPx, bool glow, float strength)
        {
            var img = c.gameObject.AddComponent<Image>();
            img.raycastTarget = false;
            Skin(img, M(radiusPx), look.panelBg, look.panelBorder, M(1f), glow, strength, ThemeRole.PanelBg, ThemeRole.PanelBorder);
        }

        /// <summary>Plain image positioned in mockup px (top-left, y down), centred on the parent, or stretched.</summary>
        static Image Box(Transform parent, string name, float x, float y, float w, float h, bool centre = false, float? stretch = null)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image));
            go.transform.SetParent(parent, false);
            var img = go.GetComponent<Image>();
            img.raycastTarget = false;
            var rt = img.rectTransform;
            if (stretch.HasValue) Stretch(rt, stretch.Value);
            else if (centre)
            {
                rt.anchorMin = rt.anchorMax = rt.pivot = new Vector2(0.5f, 0.5f);
                rt.anchoredPosition = new Vector2(M(x), -M(y));
                rt.sizeDelta = new Vector2(M(w), M(h));
            }
            else Place(rt, P(x, y, w, h));
            return img;
        }

        static TMP_Text Txt(Transform parent, string name, string text, float px, ThemeRole role, FontRole font,
            bool bold = false, float tracking = 0f, TextAlignmentOptions align = TextAlignmentOptions.MidlineLeft, Vector4? rect = null)
        {
            var t = Txt(parent, name, text, px, ThemeApplier.Resolve(look, role), font, bold, tracking, align, rect);
            var tt = t.gameObject.AddComponent<ThemeTarget>();
            tt.colorRole = role; tt.applyFont = true; tt.fontRole = font;
            return t;
        }

        static TMP_Text Txt(Transform parent, string name, string text, float px, Color color, FontRole font,
            bool bold = false, float tracking = 0f, TextAlignmentOptions align = TextAlignmentOptions.MidlineLeft, Vector4? rect = null)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(TextMeshProUGUI));
            go.transform.SetParent(parent, false);
            var t = go.GetComponent<TextMeshProUGUI>();
            t.font = font == FontRole.Brand ? brand : font == FontRole.Mono ? mono : body;
            t.text = text; t.fontSize = Fs(px); t.color = color;
            t.fontStyle = bold ? FontStyles.Bold : FontStyles.Normal;
            t.characterSpacing = tracking * 100f;   // TMP unit is 1/100 em
            t.textWrappingMode = TextWrappingModes.NoWrap;
            t.alignment = align; t.raycastTarget = false;
            if (rect.HasValue) Place(t.rectTransform, rect.Value);
            return t;
        }

        /// <summary>Status value: text.primary at full alpha; tone colour is applied at runtime by the view.</summary>
        static TMP_Text Value(Transform parent, string name, string text, float px, FontRole font, Vector4 rect)
        {
            var c = look.textPrimary; c.a = 1f;
            return Txt(parent, name, text, px, c, font, bold: true, rect: rect);
        }

        /// <summary>Auto-sizing horizontal row for pills, anchored to the left or right edge of the parent.</summary>
        static Transform Row(Transform parent, string name, float gapPx, TextAnchor align, bool leftEdge, float inset,
            float top = 0, bool fitVertical = false)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(HorizontalLayoutGroup), typeof(ContentSizeFitter));
            go.transform.SetParent(parent, false);
            var rt = (RectTransform)go.transform;
            var hlg = go.GetComponent<HorizontalLayoutGroup>();
            hlg.spacing = M(gapPx); hlg.childAlignment = align;
            hlg.childControlWidth = hlg.childControlHeight = true;
            hlg.childForceExpandWidth = hlg.childForceExpandHeight = false;
            var fit = go.GetComponent<ContentSizeFitter>();
            fit.horizontalFit = ContentSizeFitter.FitMode.PreferredSize;
            fit.verticalFit = fitVertical ? ContentSizeFitter.FitMode.PreferredSize : ContentSizeFitter.FitMode.Unconstrained;
            if (fitVertical)
            {
                rt.anchorMin = rt.anchorMax = new Vector2(leftEdge ? 0 : 1, 1);
                rt.pivot = new Vector2(leftEdge ? 0 : 1, 1);
                rt.anchoredPosition = new Vector2(leftEdge ? M(inset) : -M(inset), -M(top));
            }
            else
            {
                rt.anchorMin = new Vector2(leftEdge ? 0 : 1, 0); rt.anchorMax = new Vector2(leftEdge ? 0 : 1, 1);
                rt.pivot = new Vector2(leftEdge ? 0 : 1, 0.5f);
                rt.anchoredPosition = new Vector2(leftEdge ? M(inset) : -M(inset), 0);
                rt.sizeDelta = Vector2.zero;
            }
            return rt;
        }

        /// <summary>Rounded pill whose width follows its content (horizontal layout: dot/text children).</summary>
        static PanelSkin Pill(Transform parent, string name, float hPx, float padXPx, float gapPx, float radiusPx,
            ThemeRole fillRole, ThemeRole borderRole, float borderPx)
        {
            var img = Box(parent, name, 0, 0, 0, 0);
            var skin = Skin(img, M(radiusPx), ThemeApplier.Resolve(look, fillRole), ThemeApplier.Resolve(look, borderRole),
                M(borderPx), fillRole: fillRole, borderRole: borderRole);
            LayoutPill(img.gameObject, hPx, padXPx, gapPx);
            return skin;
        }

        static void LayoutPill(GameObject go, float hPx, float padXPx, float gapPx)
        {
            var hlg = go.AddComponent<HorizontalLayoutGroup>();
            hlg.padding = new RectOffset(Mathf.RoundToInt(M(padXPx)), Mathf.RoundToInt(M(padXPx)), 0, 0);
            hlg.spacing = M(gapPx); hlg.childAlignment = TextAnchor.MiddleCenter;
            hlg.childControlWidth = hlg.childControlHeight = true;
            hlg.childForceExpandWidth = hlg.childForceExpandHeight = false;
            var le = go.AddComponent<LayoutElement>();
            le.preferredHeight = le.minHeight = M(hPx);
        }

        static ChipView Chip(Transform parent, string name, float hPx, float padXPx, float gapPx, float radiusPx, float textPx, bool overlay)
        {
            var img = Box(parent, name, 0, 0, 0, 0);
            var skin = Skin(img, M(radiusPx), look.neutralFill, Color.clear, 0f);
            LayoutPill(img.gameObject, hPx, padXPx, gapPx);
            var dotImg = Box(img.transform, "Dot", 0, 0, 8, 8);
            var dotSkin = Skin(dotImg, M(4), look.textPrimary, Color.clear, 0f);
            var le = dotImg.gameObject.AddComponent<LayoutElement>();
            le.preferredWidth = le.preferredHeight = M(8);
            var t = Txt(img.transform, "Text", "--", textPx, look.textPrimary, FontRole.Mono, bold: true);
            var chip = img.gameObject.AddComponent<ChipView>();
            SetRef(chip, "skin", skin); SetRef(chip, "dot", dotSkin); SetRef(chip, "label", t);
            SetRef(chip, "overlayStyle", null, overlay);
            return chip;
        }

        /// <summary>White @ 92 % camera-overlay chip with mono 13/600 text.</summary>
        static TMP_Text OverlayChip(Transform parent, string name, string text)
        {
            var img = Box(parent, name, 0, 0, 0, 0);
            Skin(img, M(6), ChipWhite, Color.clear, 0f);
            LayoutPill(img.gameObject, 26, 10, 6);
            return Txt(img.transform, "Text", text, 13, ThemeRole.TextPrimary, FontRole.Mono, bold: true);
        }

        static void Relayout(Canvas c)
        {
            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate((RectTransform)c.transform);
            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate((RectTransform)c.transform);
        }

        static void SetRef(Object target, string field, Object value, object scalar = null)
        {
            var so = new SerializedObject(target);
            var p = so.FindProperty(field);
            if (p == null) throw new System.InvalidOperationException($"{target.GetType().Name} has no serialized field '{field}'.");
            if (scalar is float f) p.floatValue = f;
            else if (scalar is bool b) p.boolValue = b;
            else p.objectReferenceValue = value;
            so.ApplyModifiedPropertiesWithoutUndo();
        }

        static T Load<T>(string rel) where T : Object
        {
            var a = AssetDatabase.LoadAssetAtPath<T>($"{Root}/{rel}");
            if (a == null) throw new System.InvalidOperationException("Missing asset " + rel);
            return a;
        }
    }
}
