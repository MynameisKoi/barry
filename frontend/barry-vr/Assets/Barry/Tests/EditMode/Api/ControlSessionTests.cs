using Barry.Api;
using NUnit.Framework;

namespace Barry.Api.Tests
{
    public sealed class ControlSessionTests
    {
        [Test]
        public void Sequencer_CountsUp_AndRestartsForANewSession()
        {
            var sequencer = new CommandSequencer();
            Assert.That(sequencer.Next(), Is.EqualTo(1));
            Assert.That(sequencer.Next(), Is.EqualTo(2));
            Assert.That(sequencer.Next(), Is.EqualTo(3));

            sequencer.Reset();   // a new session starts at -1 on the relay, so the next sequence is 1 again

            Assert.That(sequencer.Last, Is.EqualTo(0));
            Assert.That(sequencer.Next(), Is.EqualTo(1));
        }

        [TestCase(409L, "Invalid or stale control session/sequence.", true)]
        [TestCase(409L, "A valid control session is required", true)]
        [TestCase(409L, "invalid or stale CONTROL SESSION/sequence", true)]
        [TestCase(409L, "Command invalidated by stop", false)]
        [TestCase(409L, "Stale command timestamp", false)]
        [TestCase(401L, "Invalid or stale control session/sequence.", false)]
        [TestCase(423L, "Emergency stop is latched", false)]
        [TestCase(0L, "Request timeout", false)]
        [TestCase(409L, null, false)]
        [TestCase(409L, "", false)]
        public void OnlyAnHttp409AboutTheSession_IsASessionRejection(long status, string error, bool expected)
        {
            Assert.That(ControlSessionErrors.IsRejected(status, error), Is.EqualTo(expected));
        }

        [Test]
        public void SessionResponse_ParsesWhatTheRelayIssues()
        {
            Assert.That(ApiJson.TryParse("{\"session_id\":\"abc123\",\"ttl_s\":10.0}", out SessionResponse session, out string error), Is.True, error);
            Assert.That(session.session_id, Is.EqualTo("abc123"));
            Assert.That(session.ttl_s, Is.EqualTo(10f));
        }
    }
}
