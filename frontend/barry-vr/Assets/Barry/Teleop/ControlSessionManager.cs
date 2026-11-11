using System;
using System.Collections;
using Barry.Api;

namespace Barry.Teleop
{
    public interface IControlSessionApi
    {
        IEnumerator AcquireSession(Action<ApiResponse<SessionResponse>> completed);
        IEnumerator ReleaseSession(Action<ApiResponse<CommandResult>> completed);
        void ClearSession();
        void ReleaseSessionBlocking();
    }

    public sealed class BarryControlSessionApi : IControlSessionApi
    {
        private readonly BarryApiClient client;

        public BarryControlSessionApi(BarryApiClient client)
        {
            this.client = client ?? throw new ArgumentNullException(nameof(client));
        }

        public IEnumerator AcquireSession(Action<ApiResponse<SessionResponse>> completed) => client.AcquireSession(completed);
        public IEnumerator ReleaseSession(Action<ApiResponse<CommandResult>> completed) => client.ReleaseSession(completed);
        public void ClearSession() => client.ClearSession();
        public void ReleaseSessionBlocking() => client.ReleaseSessionBlocking();
    }

    /// <summary>
    /// Owns the control session the relay requires for every sequenced command (motion, style, actions, posture,
    /// reset). Stop and e-stop never need a session and nothing here gates them.
    ///
    /// * Acquire once on start; retry every <see cref="RetryIntervalSeconds"/> while there is none (backend down or
    ///   restarted, Wi-Fi gap) so the app recovers by itself.
    /// * <b>No keep-alive timer.</b> POST /session always issues a NEW session and drops the previous one (single
    ///   operator, last caller wins), so a timer would steal control from the web cockpit. The session stays alive
    ///   while sequenced commands flow; if it lapses (idle &gt; ttl) the next command is refused with 409 and this
    ///   class re-acquires.
    /// * A 409 session rejection drops the dead id at once (motion is held meanwhile) and re-acquires, at most once
    ///   per <see cref="RetryIntervalSeconds"/>.
    /// * It never resumes motion itself: RobotControlService only keeps sending while the stick is still deflected.
    /// </summary>
    public sealed class ControlSessionManager
    {
        public const float RetryIntervalSeconds = 1f;

        private readonly IControlSessionApi api;
        private readonly Action<IEnumerator> startCoroutine;
        private float sinceLastAttempt = RetryIntervalSeconds;   // the first attempt is allowed immediately
        private bool started;

        public bool HasSession { get; private set; }
        public bool Acquiring { get; private set; }
        public int AcquireAttempts { get; private set; }
        public int Rejections { get; private set; }

        public event Action Acquired;
        public event Action Lost;

        public ControlSessionManager(IControlSessionApi api, Action<IEnumerator> startCoroutine)
        {
            this.api = api ?? throw new ArgumentNullException(nameof(api));
            this.startCoroutine = startCoroutine ?? throw new ArgumentNullException(nameof(startCoroutine));
        }

        /// <summary>Acquire on start.</summary>
        public void Begin()
        {
            started = true;
            TryAcquire();
        }

        public void Tick(float unscaledDeltaTime)
        {
            if (float.IsNaN(unscaledDeltaTime) || unscaledDeltaTime < 0f) unscaledDeltaTime = 0f;
            sinceLastAttempt += unscaledDeltaTime;
            // Recovery only: nothing is sent while a valid session exists.
            if (started && !HasSession && !Acquiring) TryAcquire();
        }

        /// <summary>A sequenced command came back 409 "invalid or stale session": drop the dead id and re-acquire.</summary>
        public void OnSessionRejected()
        {
            Rejections++;
            if (HasSession)
            {
                HasSession = false;
                api.ClearSession();
                Lost?.Invoke();
            }
            if (started) TryAcquire();
        }

        private void TryAcquire()
        {
            if (Acquiring || sinceLastAttempt < RetryIntervalSeconds) return;   // at most once per second
            Acquiring = true;
            sinceLastAttempt = 0f;
            AcquireAttempts++;
            startCoroutine(api.AcquireSession(response =>
            {
                Acquiring = false;
                if (!response.Success || response.Value == null) return;   // Tick retries in a second
                bool was = HasSession;
                HasSession = true;
                if (!was) Acquired?.Invoke();
            }));
        }

        /// <summary>App quit: best-effort release so the lease isn't left hanging. Never needed for stop.</summary>
        public void ReleaseOnQuit()
        {
            started = false;
            if (!HasSession) return;
            HasSession = false;
            api.ReleaseSessionBlocking();
        }
    }
}
