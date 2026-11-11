using System;
using System.Collections;
using Barry.Api;
using NUnit.Framework;

namespace Barry.Teleop.Tests
{
    public sealed class ControlSessionManagerTests
    {
        private sealed class FakeApi : IControlSessionApi
        {
            public Action<ApiResponse<SessionResponse>> AcquireCb;
            public int AcquireCalls, ClearCalls, BlockingReleases;

            public IEnumerator AcquireSession(Action<ApiResponse<SessionResponse>> completed) { AcquireCalls++; AcquireCb = completed; return Empty(); }
            public IEnumerator ReleaseSession(Action<ApiResponse<CommandResult>> completed) { return Empty(); }
            public void ClearSession() => ClearCalls++;
            public void ReleaseSessionBlocking() => BlockingReleases++;
            private static IEnumerator Empty() { yield break; }

            public void Issue(string id = "s1") =>
                AcquireCb(new ApiResponse<SessionResponse>(true, 201, new SessionResponse { session_id = id, ttl_s = 10f }, null));
            public void Fail(long code = 0) =>
                AcquireCb(new ApiResponse<SessionResponse>(false, code, null, "unreachable"));
        }

        private static (ControlSessionManager, FakeApi) Make()
        {
            var api = new FakeApi();
            return (new ControlSessionManager(api, _ => { }), api);
        }

        [Test]
        public void AcquiresOnStart_AndHasASessionOnceTheRelayAnswers()
        {
            var (manager, api) = Make();
            Assert.That(manager.HasSession, Is.False);

            manager.Begin();
            Assert.That(api.AcquireCalls, Is.EqualTo(1));
            Assert.That(manager.Acquiring, Is.True);
            Assert.That(manager.HasSession, Is.False);                 // motion stays held until the reply

            api.Issue();
            Assert.That(manager.HasSession, Is.True);
            Assert.That(manager.Acquiring, Is.False);
        }

        [Test]
        public void NeverSendsAKeepAlive_WhileTheSessionIsHeld()
        {
            // POST /session always issues a NEW session and drops the old one, so a timer would steal control.
            var (manager, api) = Make();
            manager.Begin();
            api.Issue();

            for (int i = 0; i < 600; i++) manager.Tick(0.1f);          // a full minute of idle

            Assert.That(api.AcquireCalls, Is.EqualTo(1));
            Assert.That(manager.HasSession, Is.True);
        }

        [Test]
        public void ASessionRejection_DropsTheDeadIdAndReacquires()
        {
            var (manager, api) = Make();
            manager.Begin();
            api.Issue("old");
            bool lost = false;
            manager.Lost += () => lost = true;
            manager.Tick(2f);                                          // past the once-per-second limit

            manager.OnSessionRejected();

            Assert.That(manager.HasSession, Is.False);                 // motion is held at once
            Assert.That(lost, Is.True);
            Assert.That(api.ClearCalls, Is.EqualTo(1));
            Assert.That(api.AcquireCalls, Is.EqualTo(2));
            api.Issue("new");
            Assert.That(manager.HasSession, Is.True);
        }

        [Test]
        public void ReacquiresAtMostOncePerSecond_EvenWhenManyCommandsAreRejected()
        {
            var (manager, api) = Make();
            manager.Begin();
            api.Issue();
            manager.Tick(1.5f);
            manager.OnSessionRejected();
            Assert.That(api.AcquireCalls, Is.EqualTo(2));
            api.Fail();                                                // the attempt failed right away

            for (int i = 0; i < 20; i++) manager.OnSessionRejected();  // a burst of 409s (10 Hz motion)
            Assert.That(api.AcquireCalls, Is.EqualTo(2));              // rate limited

            manager.Tick(0.4f);
            Assert.That(api.AcquireCalls, Is.EqualTo(2));
            manager.Tick(0.7f);                                        // a second has passed since the attempt
            Assert.That(api.AcquireCalls, Is.EqualTo(3));
        }

        [Test]
        public void WhileNoSessionExists_ItRetriesEverySecond_UntilItSucceeds()
        {
            var (manager, api) = Make();
            manager.Begin();
            api.Fail();                                                // backend down
            for (int second = 1; second <= 5; second++)
            {
                manager.Tick(1f);
                Assert.That(api.AcquireCalls, Is.EqualTo(1 + second));
                api.Fail();
            }

            manager.Tick(1f);
            api.Issue();                                               // backend is back (restarted)
            Assert.That(manager.HasSession, Is.True);
            int calls = api.AcquireCalls;
            for (int i = 0; i < 100; i++) manager.Tick(0.1f);
            Assert.That(api.AcquireCalls, Is.EqualTo(calls));          // and it stops asking
        }

        [Test]
        public void ARejectionWhileAnAcquireIsAlreadyInFlight_DoesNotStartAnother()
        {
            var (manager, api) = Make();
            manager.Begin();
            manager.Tick(2f);
            manager.OnSessionRejected();
            manager.OnSessionRejected();
            Assert.That(api.AcquireCalls, Is.EqualTo(1));
        }

        [Test]
        public void ARejectionBeforeStart_DoesNotAcquire()
        {
            var (manager, api) = Make();
            manager.OnSessionRejected();
            manager.Tick(5f);
            Assert.That(api.AcquireCalls, Is.EqualTo(0));
        }

        [Test]
        public void ReleaseOnQuit_ReleasesOnce_AndStopsRetrying()
        {
            var (manager, api) = Make();
            manager.Begin();
            api.Issue();

            manager.ReleaseOnQuit();
            manager.ReleaseOnQuit();

            Assert.That(api.BlockingReleases, Is.EqualTo(1));
            Assert.That(manager.HasSession, Is.False);
            manager.Tick(10f);
            Assert.That(api.AcquireCalls, Is.EqualTo(1));              // no re-acquire after quit
        }

        [Test]
        public void ReleaseOnQuit_WithoutASession_SendsNothing()
        {
            var (manager, api) = Make();
            manager.Begin();
            manager.ReleaseOnQuit();
            Assert.That(api.BlockingReleases, Is.EqualTo(0));
        }
    }
}
