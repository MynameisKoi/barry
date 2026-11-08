using System;
using System.Collections;
using System.Collections.Generic;
using Barry.Api;
using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class StatusMonitorTests
    {
        [Test]
        public void Tick_PollsStatusAndTelemetryAtConfiguredRate()
        {
            var api = new FakeStatusApi();
            var monitor = new StatusMonitor(api, _ => { }, 2f);

            monitor.Tick(0.49f);
            Assert.That(api.StatusCalls, Is.Zero);
            Assert.That(api.TelemetryCalls, Is.Zero);

            monitor.Tick(0.01f);
            Assert.That(api.StatusCalls, Is.EqualTo(1));
            Assert.That(api.TelemetryCalls, Is.EqualTo(1));
        }

        [Test]
        public void SuccessfulPolls_ExposeLatestValidatedValues()
        {
            var api = new FakeStatusApi();
            var monitor = new StatusMonitor(api, _ => { });
            var status = new StatusResponse { robot_mode = "simulator", connected = true };
            var telemetry = new TelemetryResponse { battery = 76f };
            StatusResponse statusEvent = null;
            TelemetryResponse telemetryEvent = null;
            monitor.StatusUpdated += value => statusEvent = value;
            monitor.TelemetryUpdated += value => telemetryEvent = value;

            monitor.PollNow();
            api.CompleteStatus(Success(status));
            api.CompleteTelemetry(Success(telemetry));

            Assert.That(monitor.LatestStatus, Is.SameAs(status));
            Assert.That(monitor.LatestTelemetry, Is.SameAs(telemetry));
            Assert.That(statusEvent, Is.SameAs(status));
            Assert.That(telemetryEvent, Is.SameAs(telemetry));
        }

        [Test]
        public void TwoConsecutiveTransportFailures_RaiseBackendUnreachableOnce()
        {
            var api = new FakeStatusApi();
            var monitor = new StatusMonitor(api, _ => { });
            var reasons = new List<StopReason>();
            monitor.StatusPollFailed += reasons.Add;

            FailStatusPoll(monitor, api, 0);
            Assert.That(reasons, Is.Empty);
            FailStatusPoll(monitor, api, 0);
            FailStatusPoll(monitor, api, 0);

            Assert.That(reasons, Is.EqualTo(new[] { StopReason.BackendUnreachable }));
            Assert.That(monitor.IsFailureLatched, Is.True);
        }

        [Test]
        public void HttpOrInvalidResponseFailures_RaiseStatusUnavailable()
        {
            var api = new FakeStatusApi();
            var monitor = new StatusMonitor(api, _ => { });
            StopReason raised = StopReason.None;
            monitor.StatusPollFailed += reason => raised = reason;

            FailStatusPoll(monitor, api, 503);
            FailStatusPoll(monitor, api, 503);

            Assert.That(raised, Is.EqualTo(StopReason.StatusUnavailable));
        }

        [Test]
        public void SuccessBreaksFailureSequence_ButDoesNotClearSafetyLatch()
        {
            var api = new FakeStatusApi();
            var monitor = new StatusMonitor(api, _ => { });
            int failuresRaised = 0;
            monitor.StatusPollFailed += _ => failuresRaised++;

            FailStatusPoll(monitor, api, 0);
            CompleteSuccessfulStatusPoll(monitor, api);
            Assert.That(monitor.ConsecutiveStatusFailures, Is.Zero);
            FailStatusPoll(monitor, api, 0);
            Assert.That(failuresRaised, Is.Zero);

            FailStatusPoll(monitor, api, 0);
            Assert.That(failuresRaised, Is.EqualTo(1));
            CompleteSuccessfulStatusPoll(monitor, api);

            Assert.That(monitor.IsFailureLatched, Is.True);
            Assert.That(failuresRaised, Is.EqualTo(1));
        }

        [Test]
        public void BeginNewSession_ExplicitlyClearsFailureLatch()
        {
            var api = new FakeStatusApi();
            var monitor = new StatusMonitor(api, _ => { });
            FailStatusPoll(monitor, api, 0);
            FailStatusPoll(monitor, api, 0);
            Assert.That(monitor.IsFailureLatched, Is.True);

            monitor.BeginNewSession();

            Assert.That(monitor.IsFailureLatched, Is.False);
            Assert.That(monitor.ConsecutiveStatusFailures, Is.Zero);
        }

        [Test]
        public void PollNow_DoesNotOverlapRequestsStillInFlight()
        {
            var api = new FakeStatusApi();
            var monitor = new StatusMonitor(api, _ => { });

            monitor.PollNow();
            monitor.PollNow();

            Assert.That(api.StatusCalls, Is.EqualTo(1));
            Assert.That(api.TelemetryCalls, Is.EqualTo(1));
        }

        private static void FailStatusPoll(StatusMonitor monitor, FakeStatusApi api, long statusCode)
        {
            monitor.PollNow();
            api.CompleteStatus(new ApiResponse<StatusResponse>(false, statusCode, null, "failed"));
            api.CompleteTelemetry(new ApiResponse<TelemetryResponse>(false, statusCode, null, "failed"));
        }

        private static void CompleteSuccessfulStatusPoll(StatusMonitor monitor, FakeStatusApi api)
        {
            monitor.PollNow();
            api.CompleteStatus(Success(new StatusResponse { robot_mode = "simulator", connected = true }));
            api.CompleteTelemetry(Success(new TelemetryResponse()));
        }

        private static ApiResponse<T> Success<T>(T value) where T : class =>
            new ApiResponse<T>(true, 200, value, null);

        private sealed class FakeStatusApi : IStatusApi
        {
            private Action<ApiResponse<StatusResponse>> statusCallback;
            private Action<ApiResponse<TelemetryResponse>> telemetryCallback;

            public int StatusCalls { get; private set; }
            public int TelemetryCalls { get; private set; }

            public IEnumerator GetStatus(Action<ApiResponse<StatusResponse>> completed)
            {
                StatusCalls++;
                statusCallback = completed;
                return Empty();
            }

            public IEnumerator GetTelemetry(Action<ApiResponse<TelemetryResponse>> completed)
            {
                TelemetryCalls++;
                telemetryCallback = completed;
                return Empty();
            }

            public void CompleteStatus(ApiResponse<StatusResponse> response) => statusCallback(response);
            public void CompleteTelemetry(ApiResponse<TelemetryResponse> response) => telemetryCallback(response);

            private static IEnumerator Empty()
            {
                yield break;
            }
        }
    }
}
