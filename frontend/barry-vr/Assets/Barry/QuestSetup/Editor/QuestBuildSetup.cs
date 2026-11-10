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
    /// Optional env BARRY_BACKEND_URL (e.g. http://192.168.0.156:8000) sets BarryConfig.
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
            ConfigureBackendUrl(Environment.GetEnvironmentVariable("BARRY_BACKEND_URL"));

            AssetDatabase.SaveAssets();
            Debug.Log("Barry: Quest build configured (Android, OpenXR + Meta Quest, IL2CPP/ARM64, Vulkan, min API 32, Dashboard scene).");
        }

        [MenuItem("Barry/Configure PC-VR Standalone Build")]
        public static void RunStandalone()
        {
            var target = Application.platform == RuntimePlatform.WindowsEditor ? BuildTarget.StandaloneWindows64 : BuildTarget.StandaloneLinux64;
            if (EditorUserBuildSettings.activeBuildTarget != target)
                EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Standalone, target);

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
            ConfigureBackendUrl(Environment.GetEnvironmentVariable("BARRY_BACKEND_URL"));

            AssetDatabase.SaveAssets();
            Debug.Log("Barry: PC-VR Standalone build configured (OpenXR, Touch & Touch Plus, Dashboard scene).");
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

        static void ConfigureBackendUrl(string backendUrl)
        {
            if (string.IsNullOrWhiteSpace(backendUrl)) return;
            backendUrl = backendUrl.TrimEnd('/');
            var config = AssetDatabase.LoadAssetAtPath<BarryConfig>(ConfigPath);
            if (config == null) throw new InvalidOperationException("Missing " + ConfigPath);
            config.backendUrl = backendUrl;
            config.cameraStreamUrl = backendUrl + "/api/v1/camera/stream";
            EditorUtility.SetDirty(config);
        }
    }
}
