using System;
using System.Collections.Generic;
using Barry.Config;
using Barry.Teleop;
using NUnit.Framework;
using UnityEngine;

namespace Barry.App.Tests
{
    public sealed class BarryAppWiringTests
    {
        [Test]
        public void Settings_ReadConnectionAndControlValuesFromConfig_WithEmptyMockAuth()
        {
            BarryConfig config = ScriptableObject.CreateInstance<BarryConfig>();
            try
            {
                config.backendUrl = "http://10.0.0.2:8000";
                config.cameraStreamUrl = "http://10.0.0.2:8000/api/v1/camera/stream";
                config.deadZone = 0.18f;
                config.sendRateHz = 15;

                BarryAppSettings settings = BarryAppSettings.From(config);

                Assert.That(settings.BackendUrl, Is.EqualTo(config.backendUrl));
                Assert.That(settings.CameraStreamUrl, Is.EqualTo(config.cameraStreamUrl));
                Assert.That(settings.ApiKey, Is.Null.Or.Empty);
                Assert.That(settings.DeadZone, Is.EqualTo(0.18f));
                Assert.That(settings.SendRateHz, Is.EqualTo(15f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(config);
            }
        }

        [Test]
        public void Settings_ClampControlValuesWithoutMutatingConfig()
        {
            BarryConfig config = ScriptableObject.CreateInstance<BarryConfig>();
            try
            {
                config.deadZone = 0.9f;
                config.sendRateHz = 99;

                BarryAppSettings settings = BarryAppSettings.From(config);

                Assert.That(settings.DeadZone, Is.EqualTo(0.30f));
                Assert.That(settings.SendRateHz, Is.EqualTo(20f));
                Assert.That(config.deadZone, Is.EqualTo(0.9f));
                Assert.That(config.sendRateHz, Is.EqualTo(99));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(config);
            }
        }

        [Test]
        public void Settings_RejectMissingConfigOrUrls()
        {
            Assert.Throws<ArgumentNullException>(() => BarryAppSettings.From(null));
            BarryConfig config = ScriptableObject.CreateInstance<BarryConfig>();
            try
            {
                config.backendUrl = " ";
                Assert.Throws<ArgumentException>(() => BarryAppSettings.From(config));
                config.backendUrl = "http://localhost:8000";
                config.cameraStreamUrl = null;
                Assert.Throws<ArgumentException>(() => BarryAppSettings.From(config));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(config);
            }
        }

        [Test]
        public void StopRouter_ForwardsSafetyReasonsAndIgnoresNone()
        {
            var routed = new List<StopReason>();
            var router = new BarryAppStopRouter(routed.Add);

            router.Route(StopReason.ControllerLost);
            router.Route(StopReason.StatusUnavailable);
            router.Route(StopReason.None);

            Assert.That(routed, Is.EqualTo(new[]
            {
                StopReason.ControllerLost,
                StopReason.StatusUnavailable
            }));
        }

        [Test]
        public void StopRouter_RequiresStopHandler()
        {
            Assert.Throws<ArgumentNullException>(() => new BarryAppStopRouter(null));
        }

        [Test]
        public void CameraWatchdog_ReportsWhenNoFirstFrameArrivesWithinStartupTimeout()
        {
            var watchdog = new CameraAvailabilityWatchdog(
                staleFrameSeconds: 2f,
                firstFrameTimeoutSeconds: 5f);

            Assert.That(watchdog.Update(4.99f, float.PositiveInfinity), Is.False);
            Assert.That(watchdog.Update(0.01f, float.PositiveInfinity), Is.True);
            Assert.That(watchdog.Update(10f, float.PositiveInfinity), Is.False);
        }

        [Test]
        public void CameraWatchdog_AfterFirstFrameReportsStalenessOnlyOncePerLoss()
        {
            var watchdog = new CameraAvailabilityWatchdog(
                staleFrameSeconds: 2f,
                firstFrameTimeoutSeconds: 5f);

            Assert.That(watchdog.Update(0.1f, 0.1f), Is.False);
            Assert.That(watchdog.Update(0.1f, 2.01f), Is.True);
            Assert.That(watchdog.Update(0.1f, 3f), Is.False);
        }

        [Test]
        public void CameraWatchdog_FreshFrameRearmsAfterLoss()
        {
            var watchdog = new CameraAvailabilityWatchdog(
                staleFrameSeconds: 2f,
                firstFrameTimeoutSeconds: 5f);

            watchdog.Update(0.1f, 0.1f);
            Assert.That(watchdog.Update(0.1f, 2.1f), Is.True);
            Assert.That(watchdog.Update(0.1f, 0.05f), Is.False);
            Assert.That(watchdog.Update(0.1f, 2.1f), Is.True);
        }

        [Test]
        public void CameraWatchdog_FirstFrameCancelsStartupTimeout()
        {
            var watchdog = new CameraAvailabilityWatchdog(
                staleFrameSeconds: 2f,
                firstFrameTimeoutSeconds: 5f);

            Assert.That(watchdog.Update(4.9f, float.PositiveInfinity), Is.False);
            Assert.That(watchdog.Update(0.2f, 0.01f), Is.False);
            Assert.That(watchdog.Update(0.1f, 0.1f), Is.False);
        }

        [Test]
        public void CameraWatchdog_LateFirstFrameRearmsAfterStartupLoss()
        {
            var watchdog = new CameraAvailabilityWatchdog(
                staleFrameSeconds: 2f,
                firstFrameTimeoutSeconds: 5f);

            Assert.That(watchdog.Update(5f, float.PositiveInfinity), Is.True);
            Assert.That(watchdog.Update(0.1f, 0.01f), Is.False);
            Assert.That(watchdog.Update(0.1f, 2.1f), Is.True);
        }

        [Test]
        public void CameraWatchdog_RejectsInvalidDurationsAndNegativeDeltaTime()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new CameraAvailabilityWatchdog(0f, 5f));
            Assert.Throws<ArgumentOutOfRangeException>(() => new CameraAvailabilityWatchdog(2f, 0f));
            var watchdog = new CameraAvailabilityWatchdog();
            Assert.Throws<ArgumentOutOfRangeException>(() =>
                watchdog.Update(-0.01f, float.PositiveInfinity));
        }
    }
}
