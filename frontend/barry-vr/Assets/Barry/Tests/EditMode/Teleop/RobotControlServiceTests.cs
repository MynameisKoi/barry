using System;
using System.Collections;
using System.Collections.Generic;
using Barry.Api;
using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class RobotControlServiceTests
    {
        [Test]
        public void Tick_SamplesAtConfiguredRate_AndKeepsOnlyLatestWhileRequestIsInFlight()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);

            service.SetCommand(new MotionCommand(0.2f, 0f, 0f));
            service.Tick(0.09f);
            Assert.That(transport.MotionRequests, Is.Empty);
            service.Tick(0.01f);
            Assert.That(transport.MotionRequests, Has.Count.EqualTo(1));

            service.SetCommand(new MotionCommand(0.8f, 0.1f, -0.2f));
            service.Tick(0.1f);
            Assert.That(transport.MotionRequests, Has.Count.EqualTo(1));

            transport.CompleteMotion(0, AcceptedResponse());
            service.Tick(0.1f);
            Assert.That(transport.MotionRequests, Has.Count.EqualTo(2));
            Assert.That(transport.MotionRequests[1].forward, Is.EqualTo(0.8f));
            Assert.That(transport.MotionRequests[1].strafe, Is.EqualTo(0.1f));
            Assert.That(transport.MotionRequests[1].turn, Is.EqualTo(-0.2f));
        }

        [Test]
        public void DeflectedCommand_IsSentAsKeepAliveEvenWhenUnchanged()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);

            service.SetCommand(new MotionCommand(0.4f, 0f, 0f));
            service.Tick(0.1f);
            transport.CompleteMotion(0, AcceptedResponse());
            service.Tick(0.1f);

            Assert.That(transport.MotionRequests, Has.Count.EqualTo(2));
        }

        [Test]
        public void ReturningSticksToCenter_StopsImmediately()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);

            service.SetCommand(MotionCommand.Zero);

            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.SticksCentered));
            Assert.That(transport.StopCalls, Is.EqualTo(1));
        }

        [Test]
        public void ThreeConsecutiveMotionFailures_StopForUnreliableLink()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));

            for (int index = 0; index < 3; index++)
            {
                service.Tick(0.1f);
                transport.CompleteMotion(index, FailedResponse());
            }

            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.LinkUnreliable));
            Assert.That(transport.StopCalls, Is.EqualTo(1));
        }

        [Test]
        public void SuccessfulMotion_ResetsConsecutiveFailureCount()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));

            service.Tick(0.1f);
            transport.CompleteMotion(0, FailedResponse());
            service.Tick(0.1f);
            transport.CompleteMotion(1, AcceptedResponse());

            Assert.That(service.ConsecutiveMotionFailures, Is.Zero);
            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Driving));
        }

        [Test]
        public void Stop_InvalidatesLateMotionReply_AndRearmStartsWithNoFailures()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);

            service.RequestStop(StopReason.Operator);
            transport.CompleteMotion(0, FailedResponse());

            Assert.That(service.MotionInFlight, Is.False);
            Assert.That(service.ConsecutiveMotionFailures, Is.Zero);
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.Operator));
            Assert.That(transport.StopCalls, Is.EqualTo(1));

            Assert.That(service.StateMachine.AcknowledgeStop(true), Is.True);
            Assert.That(service.StateMachine.TryArm(true, true, true, true), Is.True);
            Assert.That(service.ConsecutiveMotionFailures, Is.Zero);
        }

        [Test]
        public void Rearm_ResetsFailuresFromPreviousSession()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);
            transport.CompleteMotion(0, FailedResponse());
            Assert.That(service.ConsecutiveMotionFailures, Is.EqualTo(1));

            service.StateMachine.Disarm();
            Assert.That(service.StateMachine.TryArm(true, true, true, true), Is.True);

            Assert.That(service.ConsecutiveMotionFailures, Is.Zero);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void StopCompleted_ReportsWhetherStopWasDelivered(bool delivered)
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            StopReason completedReason = StopReason.None;
            bool? completedDelivered = null;
            service.StopCompleted += (reason, wasDelivered) =>
            {
                completedReason = reason;
                completedDelivered = wasDelivered;
            };

            service.RequestStop(StopReason.CameraUnavailable);
            transport.CompleteStop(delivered ? AcceptedResponse() : FailedResponse());

            Assert.That(completedReason, Is.EqualTo(StopReason.CameraUnavailable));
            Assert.That(completedDelivered, Is.EqualTo(delivered));
        }

        [Test]
        public void EmergencyStop_IsSentEvenWhileAMotionRequestIsPending()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);
            Assert.That(service.MotionInFlight, Is.True);

            service.RequestEmergencyStop();

            Assert.That(transport.EmergencyStopCalls, Is.EqualTo(1));
            Assert.That(service.MotionInFlight, Is.False);
            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.EmergencyStop));
        }

        [Test]
        public void EmergencyStop_IsSentEvenWhileANormalStopIsInFlight()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.RequestStop(StopReason.Operator);
            Assert.That(transport.StopCalls, Is.EqualTo(1));

            service.RequestEmergencyStop();

            Assert.That(transport.EmergencyStopCalls, Is.EqualTo(1));
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.EmergencyStop));
        }

        [Test]
        public void EmergencyStop_DropsAStaleMotionReplyAndStopsMotionSending()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);
            service.RequestEmergencyStop();

            transport.CompleteMotion(0, FailedResponse());   // stale: must not count as a link failure
            service.Tick(0.1f);

            Assert.That(service.ConsecutiveMotionFailures, Is.EqualTo(0));
            Assert.That(transport.MotionRequests, Has.Count.EqualTo(1));   // nothing sent after the e-stop
        }

        [Test]
        public void EmergencyStop_RepeatedPressesWhileInFlightSendOnce_AndReportDelivery()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            bool? delivered = null;
            int requested = 0;
            service.EmergencyStopRequested += () => requested++;
            service.EmergencyStopCompleted += d => delivered = d;

            service.RequestEmergencyStop();
            service.RequestEmergencyStop();
            Assert.That(transport.EmergencyStopCalls, Is.EqualTo(1));
            Assert.That(requested, Is.EqualTo(2));

            transport.CompleteEmergencyStop(new ApiResponse<CommandResult>(true, 200,
                new CommandResult { status = "stop_dispatched", command = "emergency_stop" }, null));
            Assert.That(delivered, Is.True);

            service.RequestEmergencyStop();   // a new press after completion is sent again
            Assert.That(transport.EmergencyStopCalls, Is.EqualTo(2));
        }

        [Test]
        public void EmergencyStop_FailedDeliveryIsReported()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            bool? delivered = null;
            service.EmergencyStopCompleted += d => delivered = d;

            service.RequestEmergencyStop();
            transport.CompleteEmergencyStop(FailedResponse());

            Assert.That(delivered, Is.False);
        }

        [Test]
        public void EmergencyStop_UpgradesAnEarlierStopReason()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            service.RequestStop(StopReason.Operator);
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.Operator));

            service.RequestEmergencyStop();
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.EmergencyStop));

            service.RequestStop(StopReason.SticksCentered);   // nothing downgrades an e-stop
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.EmergencyStop));
        }


        [Test]
        public void MissionRequired_StopsOnce_RaisesTheHint_AndIsNeverRetried()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            int hints = 0;
            service.MissionRequired += () => hints++;
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);

            transport.CompleteMotion(0, new ApiResponse<CommandResult>(true, 200,
                new CommandResult { status = "MISSION_REQUIRED", message = "An active mission is required for motion." }, null));

            Assert.That(hints, Is.EqualTo(1));
            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Stopped));
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.MissionRequired));
            Assert.That(transport.StopCalls, Is.EqualTo(1));          // a plain STOP, never gated
            Assert.That(service.ConsecutiveMotionFailures, Is.EqualTo(0));
            service.Tick(0.1f);
            service.Tick(0.1f);
            Assert.That(transport.MotionRequests, Has.Count.EqualTo(1));   // not retried
        }

        [Test]
        public void MissionRequired_AlsoRecognisedFromAnHttpErrorDetail()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingService(transport, 10f);
            bool raised = false;
            service.MissionRequired += () => raised = true;
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);

            transport.CompleteMotion(0, new ApiResponse<CommandResult>(false, 409, null, "MISSION_REQUIRED: An active mission is required for motion."));

            Assert.That(raised, Is.True);
            Assert.That(service.StateMachine.LastStopReason, Is.EqualTo(StopReason.MissionRequired));
        }

        [Test]
        public void OrdinaryFailures_AreNotMistakenForMissionRequired()
        {
            Assert.That(RobotControlService.IsMissionRequired(FailedResponse()), Is.False);
            Assert.That(RobotControlService.IsMissionRequired(AcceptedResponse()), Is.False);
            Assert.That(RobotControlService.IsMissionRequired(new ApiResponse<CommandResult>(false, 423, null, "Emergency stop is latched")), Is.False);
        }

        // ---- control session (#117): motion needs one, stop and e-stop never do

        private static ApiResponse<CommandResult> SessionRejected() =>
            new ApiResponse<CommandResult>(false, 409, null, "Invalid or stale control session/sequence.");

        private static RobotControlService DrivingWithSession(FakeTransport transport, Func<bool> ready)
        {
            var state = new TeleopStateMachine();
            Assert.That(state.TryArm(true, true, true, true), Is.True);
            state.ApplyCommand(new MotionCommand(0.1f, 0f, 0f));
            return new RobotControlService(transport, _ => { }, state, 10f, ready);
        }

        [Test]
        public void MotionIsHeld_WhileThereIsNoSession_AndSentOnceThereIs()
        {
            var transport = new FakeTransport();
            bool hasSession = false;
            RobotControlService service = DrivingWithSession(transport, () => hasSession);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));

            service.Tick(0.1f);
            service.Tick(0.1f);
            Assert.That(transport.MotionRequests, Is.Empty);
            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Driving));   // held, not stopped

            hasSession = true;
            service.Tick(0.1f);
            Assert.That(transport.MotionRequests, Has.Count.EqualTo(1));                 // the SAME push continues
        }

        [Test]
        public void ASessionRejection_IsNotALinkFailure_AndNeverStopsTheRobot()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingWithSession(transport, () => true);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));

            for (int i = 0; i < 5; i++)
            {
                service.Tick(0.1f);
                transport.CompleteMotion(i, SessionRejected());
            }

            Assert.That(service.ConsecutiveMotionFailures, Is.EqualTo(0));
            Assert.That(transport.StopCalls, Is.EqualTo(0));
            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Driving));
        }

        [Test]
        public void AfterARejection_SendingContinuesOnlyWhileTheStickIsStillDeflected()
        {
            var transport = new FakeTransport();
            bool hasSession = true;
            RobotControlService service = DrivingWithSession(transport, () => hasSession);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);
            hasSession = false;                                   // BarryApp dropped the dead id
            transport.CompleteMotion(0, SessionRejected());

            service.SetCommand(MotionCommand.Zero);               // the operator let go while the session was gone
            hasSession = true;                                    // the new session arrives
            service.Tick(0.1f);
            service.Tick(0.1f);

            Assert.That(transport.MotionRequests, Has.Count.EqualTo(1));            // nothing resumed
            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Stopped));
        }

        [Test]
        public void AnOrdinary409_StillCountsAsAFailure()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingWithSession(transport, () => true);
            service.SetCommand(new MotionCommand(0.5f, 0f, 0f));
            service.Tick(0.1f);
            transport.CompleteMotion(0, new ApiResponse<CommandResult>(false, 409, null, "Command invalidated by stop"));
            Assert.That(service.ConsecutiveMotionFailures, Is.EqualTo(1));
        }

        [Test]
        public void StopAndEmergencyStop_AreSentWithNoSession()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingWithSession(transport, () => false);

            service.RequestStop(StopReason.Operator);
            Assert.That(transport.StopCalls, Is.EqualTo(1));

            service.RequestEmergencyStop();
            Assert.That(transport.EmergencyStopCalls, Is.EqualTo(1));
        }

        [Test]
        public void StopFromTheStickCentring_IsSentWithNoSession()
        {
            var transport = new FakeTransport();
            RobotControlService service = DrivingWithSession(transport, () => false);
            service.SetCommand(MotionCommand.Zero);
            Assert.That(transport.StopCalls, Is.EqualTo(1));
            Assert.That(service.StateMachine.State, Is.EqualTo(TeleopState.Stopped));
        }

        private static RobotControlService DrivingService(FakeTransport transport, float sendRate)
        {
            var state = new TeleopStateMachine();
            Assert.That(state.TryArm(true, true, true, true), Is.True);
            state.ApplyCommand(new MotionCommand(0.1f, 0f, 0f));
            Assert.That(state.State, Is.EqualTo(TeleopState.Driving));
            return new RobotControlService(transport, _ => { }, state, sendRate);
        }

        private static ApiResponse<CommandResult> AcceptedResponse() =>
            new ApiResponse<CommandResult>(true, 200,
                new CommandResult { status = "accepted", command = "motion" }, null);

        private static ApiResponse<CommandResult> FailedResponse() =>
            new ApiResponse<CommandResult>(false, 0, null, "timeout");

        private sealed class FakeTransport : IRobotCommandTransport
        {
            private readonly List<Action<ApiResponse<CommandResult>>> motionCallbacks =
                new List<Action<ApiResponse<CommandResult>>>();
            private Action<ApiResponse<CommandResult>> stopCallback;
            private Action<ApiResponse<CommandResult>> emergencyCallback;

            public readonly List<MotionRequest> MotionRequests = new List<MotionRequest>();
            public int StopCalls { get; private set; }
            public int EmergencyStopCalls { get; private set; }

            public IEnumerator SendMotion(MotionRequest request, Action<ApiResponse<CommandResult>> completed)
            {
                MotionRequests.Add(request);
                motionCallbacks.Add(completed);
                return Empty();
            }

            public IEnumerator Stop(Action<ApiResponse<CommandResult>> completed)
            {
                StopCalls++;
                stopCallback = completed;
                return Empty();
            }

            public IEnumerator EmergencyStop(Action<ApiResponse<CommandResult>> completed)
            {
                EmergencyStopCalls++;
                emergencyCallback = completed;
                return Empty();
            }

            public void CompleteEmergencyStop(ApiResponse<CommandResult> response) => emergencyCallback(response);

            public void CompleteMotion(int index, ApiResponse<CommandResult> response) =>
                motionCallbacks[index](response);

            public void CompleteStop(ApiResponse<CommandResult> response) => stopCallback(response);

            private static IEnumerator Empty()
            {
                yield break;
            }
        }
    }
}
