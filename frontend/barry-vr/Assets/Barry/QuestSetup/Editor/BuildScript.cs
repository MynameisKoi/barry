using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.Rendering;

namespace Barry.Build.Editor
{
    /// <summary>
    /// Headless Quest APK build. Batch mode:
    ///   Unity -batchmode -quit -projectPath . -buildTarget Android
    ///         -executeMethod Barry.Build.Editor.BuildScript.BuildQuest -logFile build.log
    /// Optional: -buildOutput &lt;path&gt; (default Builds/Barry.apk). Exits non-zero on failure.
    /// </summary>
    public static class BuildScript
    {
        const string DefaultOutput = "Builds/Barry.apk";

        public static void BuildQuest()
        {
            int code = 1;
            try
            {
                QuestBuildSetup.Run();

                var scenes = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray();
                if (scenes.Length == 0) throw new InvalidOperationException("No enabled scenes in EditorBuildSettings.");

                var output = ArgValue("-buildOutput") ?? DefaultOutput;
                var dir = Path.GetDirectoryName(Path.GetFullPath(output));
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

                EditorUserBuildSettings.buildAppBundle = false;   // APK, not AAB
                BuildReport report;
                var restore = UseMobilePipelineOnly();
                try
                {
                    report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
                    {
                        scenes = scenes,
                        locationPathName = output,
                        target = BuildTarget.Android,
                        options = BuildOptions.None,
                    });
                }
                finally { restore(); }

                var summary = report.summary;
                Debug.Log($"Barry: build {summary.result}, {summary.totalErrors} errors, {summary.totalWarnings} warnings -> {output}");
                code = summary.result == BuildResult.Succeeded ? 0 : 1;
            }
            catch (Exception e)
            {
                Debug.LogError("Barry: BuildQuest failed: " + e);
            }
            EditorApplication.Exit(code);
        }

        /// <summary>
        /// The OpenXR Meta Quest validator scans GraphicsSettings.allConfiguredRenderPipelines, which
        /// includes quality levels excluded for Android, and warns about PC_Renderer's SSAO. For the
        /// duration of the build, point every non-Mobile quality level at the Mobile pipeline asset
        /// (in memory only; the returned action restores the original assets, nothing is saved).
        /// </summary>
        static Action UseMobilePipelineOnly()
        {
            var mobile = AssetDatabase.LoadAssetAtPath<RenderPipelineAsset>(QuestBuildSetup.MobilePipelineAsset);
            var originals = new RenderPipelineAsset[QualitySettings.names.Length];
            for (int i = 0; i < originals.Length; i++)
            {
                originals[i] = QualitySettings.GetRenderPipelineAssetAt(i);
                if (originals[i] != mobile) QualitySettings.SetRenderPipelineAssetAt(i, mobile);
            }
            return () =>
            {
                for (int i = 0; i < originals.Length; i++)
                    QualitySettings.SetRenderPipelineAssetAt(i, originals[i]);
            };
        }

        static string ArgValue(string name)
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == name) return args[i + 1];
            return null;
        }
    }
}
