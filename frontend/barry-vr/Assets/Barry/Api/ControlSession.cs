using System;

namespace Barry.Api
{
    /// <summary>
    /// The per-session command counter. The relay accepts a sequenced command only when its sequence is higher
    /// than the last one it saw for the CURRENT session, and a new session restarts at -1, so the counter must
    /// restart whenever a new session is issued.
    /// </summary>
    public sealed class CommandSequencer
    {
        private long last;

        public long Last => last;

        public long Next() => ++last;

        public void Reset() => last = 0;
    }

    public static class ControlSessionErrors
    {
        /// <summary>
        /// The relay's reply to a sequenced command with no valid session: HTTP 409
        /// "Invalid or stale control session/sequence." (the Pi's own wording, "A valid control session is required",
        /// is matched too). Other 409s, like "Command invalidated by stop", are NOT session errors.
        /// </summary>
        public static bool IsRejected(long statusCode, string error)
        {
            if (statusCode != 409 || string.IsNullOrEmpty(error)) return false;
            return error.IndexOf("control session", StringComparison.OrdinalIgnoreCase) >= 0;
        }
    }
}
