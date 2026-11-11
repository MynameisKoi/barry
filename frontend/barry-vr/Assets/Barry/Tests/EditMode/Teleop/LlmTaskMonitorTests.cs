using System;
using System.Collections;
using Barry.Api;
using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class LlmTaskMonitorTests
    {
        private sealed class FakeApi : ILlmApi
        {
            public Action<ApiResponse<LlmStartResponse>> StartCb;
            public Action<ApiResponse<LlmTaskStatus>> PollCb;
            public Action<ApiResponse<LlmCancelResponse>> CancelCb;
            public int StartCalls, PollCalls, CancelCalls;
            public string LastGoal;

            public IEnumerator StartLlmTask(string goal, Action<ApiResponse<LlmStartResponse>> c) { StartCalls++; LastGoal = goal; StartCb = c; return Empty(); }
            public IEnumerator GetLlmTask(Action<ApiResponse<LlmTaskStatus>> c) { PollCalls++; PollCb = c; return Empty(); }
            public IEnumerator CancelLlmTask(Action<ApiResponse<LlmCancelResponse>> c) { CancelCalls++; CancelCb = c; return Empty(); }
            private static IEnumerator Empty() { yield break; }
        }

        private static LlmTaskStatus Task(string state, int turns = 1) => new LlmTaskStatus { id = 1, state = state, turns = turns, steps = new LlmStep[0] };
        private static ApiResponse<T> Ok<T>(T v) where T : class => new ApiResponse<T>(true, 200, v, null);
        private static ApiResponse<T> Fail<T>(string e, long code = 503) where T : class => new ApiResponse<T>(false, code, null, e);

        private static (LlmTaskMonitor, FakeApi) Make()
        {
            var api = new FakeApi();
            return (new LlmTaskMonitor(api, _ => { }), api);
        }

        private static (LlmTaskMonitor, FakeApi) Running()
        {
            var (monitor, api) = Make();
            monitor.Start("go");
            api.StartCb(Ok(new LlmStartResponse { status = "started", task = Task("running") }));
            return (monitor, api);
        }

        [Test]
        public void NeverPolls_WhileNoTaskIsRunning()
        {
            var (monitor, api) = Make();
            for (int i = 0; i < 20; i++) monitor.Tick(0.5f);
            Assert.That(api.PollCalls, Is.EqualTo(0));
            Assert.That(api.StartCalls, Is.EqualTo(0));       // and nothing auto-starts
        }

        [Test]
        public void PollsAtTwoHz_WhileRunning_WithOneRequestInFlight()
        {
            var (monitor, api) = Running();
            monitor.Tick(0.4f);
            Assert.That(api.PollCalls, Is.EqualTo(0));
            monitor.Tick(0.1f);
            Assert.That(api.PollCalls, Is.EqualTo(1));
            monitor.Tick(1f);
            Assert.That(api.PollCalls, Is.EqualTo(1));        // still in flight
            api.PollCb(Ok(Task("running", 2)));
            monitor.Tick(0.5f);
            Assert.That(api.PollCalls, Is.EqualTo(2));
            Assert.That(monitor.Latest.turns, Is.EqualTo(2));
        }

        [Test]
        public void PollingStopsOnceTheTaskFinishes()
        {
            var (monitor, api) = Running();
            monitor.Tick(0.5f);
            api.PollCb(Ok(Task("done")));
            Assert.That(monitor.IsRunning, Is.False);
            for (int i = 0; i < 10; i++) monitor.Tick(0.5f);
            Assert.That(api.PollCalls, Is.EqualTo(1));
        }

        [Test]
        public void ACancelByHandInputOnTheBackend_IsReflectedOnTheNextPoll()
        {
            var (monitor, api) = Running();
            monitor.Tick(0.5f);
            api.PollCb(Ok(new LlmTaskStatus { id = 1, state = "cancelled", message = "Cancelled", steps = new LlmStep[0] }));
            Assert.That(monitor.Latest.state, Is.EqualTo("cancelled"));
            Assert.That(monitor.IsRunning, Is.False);
        }

        [Test]
        public void Start_SendsTheTrimmedGoalOnce_AndShowsTheRunningTask()
        {
            var (monitor, api) = Make();
            Assert.That(monitor.Start("  Walk forward 2 seconds "), Is.True);
            Assert.That(api.LastGoal, Is.EqualTo("Walk forward 2 seconds"));
            Assert.That(monitor.Starting, Is.True);
            Assert.That(monitor.Start("again"), Is.False);     // not while a start is in flight
            Assert.That(api.StartCalls, Is.EqualTo(1));
            api.StartCb(Ok(new LlmStartResponse { status = "started", task = Task("running") }));
            Assert.That(monitor.Starting, Is.False);
            Assert.That(monitor.IsRunning, Is.True);
        }

        [TestCase("")]
        [TestCase("   ")]
        [TestCase(null)]
        public void Start_NeedsAGoal(string goal)
        {
            var (monitor, api) = Make();
            Assert.That(monitor.Start(goal), Is.False);
            Assert.That(api.StartCalls, Is.EqualTo(0));
        }

        [Test]
        public void StartingWhileRunning_IsAllowed_AndFlagsThatTheBackendReplacesTheOldTask()
        {
            var (monitor, api) = Running();
            Assert.That(monitor.Start("something else"), Is.True);
            Assert.That(monitor.ReplacedPrevious, Is.True);
            api.StartCb(Ok(new LlmStartResponse { status = "started", task = new LlmTaskStatus { id = 2, state = "running", steps = new LlmStep[0] } }));
            Assert.That(monitor.Latest.id, Is.EqualTo(2));
        }

        [Test]
        public void AFailedStart_ShowsTheError_AndNeverRunsAnything()
        {
            var (monitor, api) = Make();
            monitor.Start("go");
            api.StartCb(Fail<LlmStartResponse>("LLM control is not configured"));
            Assert.That(monitor.Error, Is.EqualTo("LLM control is not configured"));
            Assert.That(monitor.IsRunning, Is.False);
            Assert.That(monitor.Start("go"), Is.True);        // can try again
        }

        [Test]
        public void Cancel_CallsTheApi_OnlyWhileRunning_AndOnlyOnce()
        {
            var (idle, idleApi) = Make();
            Assert.That(idle.Cancel(), Is.False);
            Assert.That(idleApi.CancelCalls, Is.EqualTo(0));

            var (monitor, api) = Running();
            Assert.That(monitor.Cancel(), Is.True);
            Assert.That(monitor.Cancelling, Is.True);
            Assert.That(monitor.Cancel(), Is.False);
            Assert.That(api.CancelCalls, Is.EqualTo(1));
            api.CancelCb(Ok(new LlmCancelResponse { cancelled = true, task = Task("cancelled") }));
            Assert.That(monitor.Cancelling, Is.False);
            Assert.That(monitor.Latest.state, Is.EqualTo("cancelled"));
        }

        [Test]
        public void AFailedCancel_ShowsTheError_AndTheTaskStillCountsAsRunningUntilPolled()
        {
            var (monitor, api) = Running();
            monitor.Cancel();
            api.CancelCb(Fail<LlmCancelResponse>("offline", 0));
            Assert.That(monitor.Error, Is.EqualTo("offline"));
            Assert.That(monitor.IsRunning, Is.True);
            Assert.That(monitor.Cancel(), Is.True);           // the operator can press it again
        }

        [Test]
        public void AFailedPoll_KeepsTheLastTask_AndRecordsTheError()
        {
            var (monitor, api) = Running();
            monitor.Tick(0.5f);
            api.PollCb(Fail<LlmTaskStatus>("unreachable", 0));
            Assert.That(monitor.IsRunning, Is.True);
            Assert.That(monitor.Error, Is.EqualTo("unreachable"));
            monitor.Tick(0.5f);
            api.PollCb(Ok(Task("running", 3)));
            Assert.That(monitor.Error, Is.Null);
        }
    }
}
