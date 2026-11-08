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

            public readonly List<MotionRequest> MotionRequests = new List<MotionRequest>();
            public int StopCalls { get; private set; }

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
