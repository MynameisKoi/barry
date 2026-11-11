using System;
using System.Collections;
using System.Collections.Generic;
using Barry.Api;
using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class MissionMonitorTests
    {
        private sealed class FakeApi : IMissionApi
        {
            public Action<ApiResponse<MissionStatus>> StatusCb, StartCb, StopCb;
            public Action<ApiResponse<OperatorsResponse>> OperatorsCb;
            public int StatusCalls, OperatorCalls, StartCalls, StopCalls;
            public MissionStartRequest LastStart;
            public string LastStopReason;

            public IEnumerator GetMissionStatus(Action<ApiResponse<MissionStatus>> completed) { StatusCalls++; StatusCb = completed; return Empty(); }
            public IEnumerator GetOperators(Action<ApiResponse<OperatorsResponse>> completed) { OperatorCalls++; OperatorsCb = completed; return Empty(); }
            public IEnumerator StartMission(MissionStartRequest body, Action<ApiResponse<MissionStatus>> completed) { StartCalls++; LastStart = body; StartCb = completed; return Empty(); }
            public IEnumerator StopMission(string reason, Action<ApiResponse<MissionStatus>> completed) { StopCalls++; LastStopReason = reason; StopCb = completed; return Empty(); }
            private static IEnumerator Empty() { yield break; }
        }

        private static ApiResponse<T> Ok<T>(T value) where T : class => new ApiResponse<T>(true, 200, value, null);
        private static ApiResponse<T> Fail<T>(string error, long code = 400) where T : class => new ApiResponse<T>(false, code, null, error);
        private static MissionStatus Active() => new MissionStatus { active = true, zone = "Zone-A", operator_id = "op1", operator_name = "Ada" };
        private static MissionStatus Idle() => new MissionStatus { active = false };

        private static (MissionMonitor, FakeApi) Make()
        {
            var api = new FakeApi();
            return (new MissionMonitor(api, _ => { }), api);
        }

        [Test]
        public void Polls_AtOneHz_WithOneRequestInFlight()
        {
            var (monitor, api) = Make();
            monitor.Tick(0.5f);
            Assert.That(api.StatusCalls, Is.EqualTo(0));
            monitor.Tick(0.5f);
            Assert.That(api.StatusCalls, Is.EqualTo(1));
            monitor.Tick(1f);
            Assert.That(api.StatusCalls, Is.EqualTo(1));          // still in flight
            api.StatusCb(Ok(Idle()));
            monitor.Tick(1f);
            Assert.That(api.StatusCalls, Is.EqualTo(2));
        }

        [Test]
        public void StatusIsUnknownUntilAPollSucceeds_AndAgainAfterAFailedPoll()
        {
            var (monitor, api) = Make();
            Assert.That(monitor.StatusKnown, Is.False);
            monitor.PollNow();
            api.StatusCb(Ok(Active()));
            Assert.That(monitor.IsActive, Is.True);
            monitor.PollNow();
            api.StatusCb(Fail<MissionStatus>("unreachable", 0));
            Assert.That(monitor.StatusKnown, Is.False);
            Assert.That(monitor.IsActive, Is.False);
        }

        [Test]
        public void OperatorsLoadWhileNoMissionIsActive_AndRetryUntilTheyArrive()
        {
            var (monitor, api) = Make();
            monitor.PollNow();
            Assert.That(api.OperatorCalls, Is.EqualTo(1));
            api.OperatorsCb(Fail<OperatorsResponse>("offline", 0));
            Assert.That(monitor.Operators, Is.Null);
            Assert.That(monitor.OperatorsError, Is.EqualTo("offline"));
            api.StatusCb(Ok(Idle()));
            monitor.PollNow();
            Assert.That(api.OperatorCalls, Is.EqualTo(2));
            api.OperatorsCb(Ok(new OperatorsResponse { operators = new[] { new OperatorInfo { operator_id = "op1", name = "Ada" } } }));
            Assert.That(monitor.Operators, Has.Length.EqualTo(1));
            monitor.PollNow();
            Assert.That(api.OperatorCalls, Is.EqualTo(2));          // loaded: no more requests
        }

        [Test]
        public void StartMission_SendsOperatorAndZone_OnceAndBecomesActive()
        {
            var (monitor, api) = Make();
            monitor.PollNow();
            api.StatusCb(Ok(Idle()));

            Assert.That(monitor.StartMission("op1", "Zone-B"), Is.True);
            Assert.That(api.LastStart.operator_id, Is.EqualTo("op1"));
            Assert.That(api.LastStart.zone, Is.EqualTo("Zone-B"));
            Assert.That(monitor.StartMission("op1", "Zone-B"), Is.False);   // already starting
            Assert.That(api.StartCalls, Is.EqualTo(1));

            api.StartCb(Ok(new MissionStatus { active = true, zone = "Zone-B", operator_id = "op1" }));
            Assert.That(monitor.IsActive, Is.True);
            Assert.That(monitor.Starting, Is.False);
            Assert.That(monitor.StartMission("op1", "Zone-A"), Is.False);   // one mission at a time
        }

        [Test]
        public void FailedStart_KeepsTheErrorAndStaysInactive()
        {
            var (monitor, api) = Make();
            monitor.PollNow();
            api.StatusCb(Ok(Idle()));
            monitor.StartMission("op1", "Zone-A");
            api.StartCb(Fail<MissionStatus>("Operator not authorized"));
            Assert.That(monitor.IsActive, Is.False);
            Assert.That(monitor.StartError, Is.EqualTo("Operator not authorized"));
            Assert.That(monitor.StartMission("op1", "Zone-A"), Is.True);     // can retry
        }

        [TestCase(null, "Zone-A")]
        [TestCase("op1", "")]
        [TestCase(" ", "Zone-A")]
        public void StartMission_NeedsOperatorAndZone(string op, string zone)
        {
            var (monitor, api) = Make();
            Assert.That(monitor.StartMission(op, zone), Is.False);
            Assert.That(api.StartCalls, Is.EqualTo(0));
        }

        [Test]
        public void EndMission_NeedsTwoPresses_FirstOnlyAsksForConfirmation()
        {
            var (monitor, api) = Make();
            monitor.PollNow();
            api.StatusCb(Ok(Active()));

            Assert.That(monitor.PressEndMission(), Is.EqualTo(ConfirmStep.AwaitConfirmation));
            Assert.That(api.StopCalls, Is.EqualTo(0));
            Assert.That(monitor.IsActive, Is.True);

            Assert.That(monitor.PressEndMission(), Is.EqualTo(ConfirmStep.Confirmed));
            Assert.That(api.StopCalls, Is.EqualTo(1));
            Assert.That(api.LastStopReason, Is.EqualTo(MissionMonitor.EndReason));
            api.StopCb(Ok(new MissionStatus { active = false, status = "ended" }));
            Assert.That(monitor.IsActive, Is.False);
            Assert.That(monitor.EndGate.Busy, Is.False);
        }

        [Test]
        public void AnUnconfirmedEnd_LapsesAndNothingIsSent()
        {
            var (monitor, api) = Make();
            monitor.PollNow();
            api.StatusCb(Ok(Active()));
            monitor.PressEndMission();
            monitor.Tick(3.1f);
            Assert.That(monitor.PressEndMission(), Is.EqualTo(ConfirmStep.AwaitConfirmation));
            Assert.That(api.StopCalls, Is.EqualTo(0));
        }

        [Test]
        public void AFailedEnd_KeepsTheMissionActiveAndShowsTheError()
        {
            var (monitor, api) = Make();
            monitor.PollNow();
            api.StatusCb(Ok(Active()));
            monitor.PressEndMission();
            monitor.PressEndMission();
            api.StopCb(Fail<MissionStatus>("backend down", 0));
            Assert.That(monitor.IsActive, Is.True);
            Assert.That(monitor.EndError, Is.EqualTo("backend down"));
        }

        [Test]
        public void EndMission_DoesNothingWithoutAnActiveMission()
        {
            var (monitor, api) = Make();
            Assert.That(monitor.PressEndMission(), Is.EqualTo(ConfirmStep.None));
            Assert.That(api.StopCalls, Is.EqualTo(0));
        }
    }
}
