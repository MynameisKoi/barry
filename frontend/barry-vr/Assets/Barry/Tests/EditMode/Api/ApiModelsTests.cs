using Barry.Api;
using NUnit.Framework;

namespace Barry.Tests.EditMode.Api
{
    public sealed class ApiModelsTests
    {
        [Test]
        public void CommandResult_AcceptsBackendSuccessStatuses()
        {
            Assert.That(ApiJson.TryParse("{\"status\":\"accepted\",\"command\":\"motion\",\"message\":\"ok\"}", out CommandResult result, out string error), Is.True, error);
            Assert.That(result.Accepted, Is.True);
        }

        [Test]
        public void CommandResult_RejectsMissingRequiredField()
        {
            Assert.That(ApiJson.TryParse("{\"command\":\"motion\"}", out CommandResult _, out string error), Is.False);
            Assert.That(error, Does.Contain("status"));
        }

        [Test]
        public void Actions_RejectInvalidEntries()
        {
            Assert.That(ApiJson.TryParse("{\"actions\":[{\"id\":0,\"name\":\"\"}]}", out ActionsResponse _, out string error), Is.False);
            Assert.That(error, Does.Contain("invalid action"));
        }

        [Test]
        public void Telemetry_ParsesBackendShape()
        {
            const string json = "{\"battery\":82.5,\"orientation\":{\"roll\":1,\"pitch\":2,\"yaw\":3},\"firmware\":\"1.0\",\"read_errors\":[],\"control_state\":{\"intensity\":\"medium\",\"gait\":\"trot\",\"moving\":false}}";
            Assert.That(ApiJson.TryParse(json, out TelemetryResponse telemetry, out string error), Is.True, error);
            Assert.That(telemetry.battery, Is.EqualTo(82.5f));
            Assert.That(telemetry.orientation.pitch, Is.EqualTo(2f));
        }

        [Test]
        public void CameraStatus_RejectsInvalidResolution()
        {
            const string json = "{\"connected\":true,\"resolution\":{\"width\":0,\"height\":480},\"target_fps\":15}";
            Assert.That(ApiJson.TryParse(json, out CameraStatusResponse _, out string error), Is.False);
            Assert.That(error, Does.Contain("resolution"));
        }
    }
}
