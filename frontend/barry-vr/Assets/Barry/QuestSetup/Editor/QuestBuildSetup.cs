using System;
using Barry.Config;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.XR.Management;
using UnityEditor.XR.Management.Metadata;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.XR.Management;
using UnityEngine.XR.OpenXR;
using UnityEngine.XR.OpenXR.Features.Interactions;
using UnityEngine.XR.OpenXR.Features.MetaQuestSupport;

namespace Barry.Build.Editor
{
    /// <summary>
    /// One-click Quest 3S setup: Android target, OpenXR + Meta Quest feature and Touch
    /// controller profiles, Quest player settings, Dashboard as the only scene and the
    /// backend URL. Safe to re-run. Batch mode:
    ///   Unity -batchmode -quit -buildTarget Android -projectPath frontend/barry-vr
    ///         -executeMethod Barry.Build.Editor.QuestBuildSetup.Run
    /// Optional env BARRY_BACKEND_URL (e.g. http://192.168.0.156:8000),
    /// BARRY_ROBOT_KEY and BARRY_CAMERA_KEY set BarryConfig.
    /// </summary>
    public static class QuestBuildSetup
    {
        const string DashboardScene = "Assets/Barry/Scenes/Dashboard.unity";
        const string ConfigPath = "Assets/Barry/Config/BarryConfig.asset";
        const string XrSettingsPath = "Assets/XR/XRGeneralSettingsPerBuildTarget.asset";
        const string XrSettingsKey = "com.unity.xr.management.loader_settings";
        const string OpenXrLoader = "UnityEngine.XR.OpenXR.OpenXRLoader";

        [MenuItem("Barry/Configure Quest Build")]
        public static void Run()
        {
            if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android)
                EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);

            ConfigurePlayer();
            ConfigureXrLoader();
            ConfigureOpenXrFeatures();
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(DashboardScene, true) };
            ConfigureBackend(
                Environment.GetEnvironmentVariable("BARRY_BACKEND_URL"),
                Environment.GetEnvironmentVariable("BARRY_ROBOT_KEY"),
                Environment.GetEnvironmentVariable("BARRY_CAMERA_KEY"));

            AssetDatabase.SaveAssets();
            Debug.Log("Barry: Quest build configured (Android, OpenXR + Meta Quest, IL2CPP/ARM64, Vulkan, min API 32, Dashboard scene).");
        }

        const string PcvrDefaultOutput = "Builds/PCVR/Barry.exe";

        /// <summary>
        /// Configure + build the Windows PC-VR player (Option 3: Quest Link / Air Link).
        /// Always Windows x64 + Mono so it can be cross-built from a Mac Editor
        /// (needs the "Windows Build Support (Mono)" module). Batch mode:
        ///   Unity -batchmode -quit -projectPath frontend/barry-vr
        ///         -executeMethod Barry.Build.Editor.QuestBuildSetup.BuildPCVR [-buildOutput path/Barry.exe]
        /// </summary>
        [MenuItem("Barry/Build PC-VR (Windows)")]
        public static void BuildPCVR()
        {
            if (!BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Standalone, BuildTarget.StandaloneWindows64))
                Fail("Windows Build Support (Mono) is not installed for this Unity version. Add it in Unity Hub → Installs → Add modules.");

            RunStandalone();

            var output = PcvrDefaultOutput;
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == "-buildOutput") output = args[i + 1];

            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { DashboardScene },
                locationPathName = output,
                target = BuildTarget.StandaloneWindows64,
                targetGroup = BuildTargetGroup.Standalone,
                options = BuildOptions.None,
            });
            var summary = report.summary;
            if (summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
                Fail($"PC-VR build {summary.result} with {summary.totalErrors} error(s).");
            Debug.Log($"Barry: PC-VR build succeeded → {output} ({summary.totalSize / (1024 * 1024)} MB). Zip the whole folder, not just the .exe.");
        }

        static void Fail(string message)
        {
            Debug.LogError("Barry: " + message);
            if (Application.isBatchMode) EditorApplication.Exit(1);
            throw new InvalidOperationException(message);
        }

        [MenuItem("Barry/Configure PC-VR Standalone Build")]
        public static void RunStandalone()
        {
            // Quest Link / Air Link run only on Windows, so the PC-VR player is always Win64,
            // even when configured from a Mac Editor.
            const BuildTarget target = BuildTarget.StandaloneWindows64;
            if (EditorUserBuildSettings.activeBuildTarget != target)
                EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Standalone, target);
            // IL2CPP can't cross-compile Windows from macOS; Mono works from either host.
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
            PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed;

            ConfigureXrLoader();
            UnityEditor.XR.OpenXR.Features.FeatureHelpers.RefreshFeatures(BuildTargetGroup.Standalone);
            var oxr = OpenXRSettings.GetSettingsForBuildTargetGroup(BuildTargetGroup.Standalone);
            if (oxr != null)
            {
                Enable<OculusTouchControllerProfile>(oxr);
                Enable<MetaQuestTouchPlusControllerProfile>(oxr);
                EditorUtility.SetDirty(oxr);
            }
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(DashboardScene, true) };
            ConfigureBackend(
                Environment.GetEnvironmentVariable("BARRY_BACKEND_URL"),
                Environment.GetEnvironmentVariable("BARRY_ROBOT_KEY"),
                Environment.GetEnvironmentVariable("BARRY_CAMERA_KEY"));

            AssetDatabase.SaveAssets();
            Debug.Log("Barry: PC-VR Standalone build configured (Windows x64, Mono, OpenXR, Touch & Touch Plus, Dashboard scene).");
        }

        static void ConfigurePlayer()
        {
            var android = NamedBuildTarget.Android;
            PlayerSettings.companyName = "Barry";
            PlayerSettings.productName = "Barry";
            PlayerSettings.SetApplicationIdentifier(android, "com.barry.operator");
            PlayerSettings.SetScriptingBackend(android, ScriptingImplementation.IL2CPP);
            PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
            PlayerSettings.Android.minSdkVersion = (AndroidSdkVersions)32;
            PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.Android, false);
            PlayerSettings.SetGraphicsAPIs(BuildTarget.Android, new[] { GraphicsDeviceType.Vulkan });
            PlayerSettings.colorSpace = ColorSpace.Linear;
            // The backend is plain HTTP on the LAN; the app needs internet permission and cleartext.
            PlayerSettings.Android.forceInternetPermission = true;
            PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed;
        }

        static void ConfigureXrLoader()
        {
            EditorBuildSettings.TryGetConfigObject(XrSettingsKey, out XRGeneralSettingsPerBuildTarget perTarget);
            if (perTarget == null)
            {
                perTarget = AssetDatabase.LoadAssetAtPath<XRGeneralSettingsPerBuildTarget>(XrSettingsPath);
                if (perTarget == null)
                {
                    perTarget = ScriptableObject.CreateInstance<XRGeneralSettingsPerBuildTarget>();
                    AssetDatabase.CreateAsset(perTarget, XrSettingsPath);
                }
                EditorBuildSettings.AddConfigObject(XrSettingsKey, perTarget, true);
            }

            foreach (var group in new[] { BuildTargetGroup.Android, BuildTargetGroup.Standalone })
            {
                if (!perTarget.HasManagerSettingsForBuildTarget(group))
                    perTarget.CreateDefaultManagerSettingsForBuildTarget(group);
                var settings = perTarget.SettingsForBuildTarget(group);
                settings.InitManagerOnStart = true;
                if (!XRPackageMetadataStore.IsLoaderAssigned(OpenXrLoader, group))
                    XRPackageMetadataStore.AssignLoader(settings.Manager, OpenXrLoader, group);
                EditorUtility.SetDirty(settings);
                EditorUtility.SetDirty(settings.Manager);
            }
            EditorUtility.SetDirty(perTarget);
        }

        static void ConfigureOpenXrFeatures()
        {
            // In batch mode the feature list is empty until the OpenXR editor scans for features.
            UnityEditor.XR.OpenXR.Features.FeatureHelpers.RefreshFeatures(BuildTargetGroup.Android);
            var oxr = OpenXRSettings.GetSettingsForBuildTargetGroup(BuildTargetGroup.Android);
            if (oxr == null) throw new InvalidOperationException("OpenXR settings for Android not found.");
            Enable<MetaQuestFeature>(oxr);
            Enable<OculusTouchControllerProfile>(oxr);
            Enable<MetaQuestTouchPlusControllerProfile>(oxr);   // Quest 3 / 3S controllers
            EditorUtility.SetDirty(oxr);
        }

        static void Enable<T>(OpenXRSettings oxr) where T : UnityEngine.XR.OpenXR.Features.OpenXRFeature
        {
            var feature = oxr.GetFeature<T>();
            if (feature == null) throw new InvalidOperationException("OpenXR feature not found: " + typeof(T).Name);
            feature.enabled = true;
            EditorUtility.SetDirty(feature);
        }

        static void ConfigureBackend(string backendUrl, string robotApiKey, string cameraApiKey)
        {
            if (string.IsNullOrWhiteSpace(backendUrl) && robotApiKey == null && cameraApiKey == null) return;
            var config = AssetDatabase.LoadAssetAtPath<BarryConfig>(ConfigPath);
            if (config == null) throw new InvalidOperationException("Missing " + ConfigPath);
            if (!string.IsNullOrWhiteSpace(backendUrl))
            {
                backendUrl = backendUrl.TrimEnd('/');
                config.backendUrl = backendUrl;
                config.cameraStreamUrl = backendUrl + "/api/v1/camera/stream";
            }
            if (robotApiKey != null) config.robotApiKey = robotApiKey;
            if (cameraApiKey != null) config.cameraApiKey = cameraApiKey;
            EditorUtility.SetDirty(config);
        }
    }
}
