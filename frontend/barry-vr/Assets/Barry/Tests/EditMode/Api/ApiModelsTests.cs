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

        [Test]
        public void PostureRequest_SerializesTheFieldsThePiExpects()
        {
            string json = UnityEngine.JsonUtility.ToJson(new PostureRequest("medium", 1.5f, 0f, -2f));
            Assert.That(json, Does.Contain("\"height\":\"medium\""));
            Assert.That(json, Does.Contain("\"roll\":1.5"));
            Assert.That(json, Does.Contain("\"pitch\":0"));
            Assert.That(json, Does.Contain("\"yaw\":-2"));
        }

        [Test]
        public void TelemetryControlState_ExposesTheEmergencyLatch()
        {
            Assert.That(ApiJson.TryParse(
                "{\"battery\":80,\"orientation\":{},\"control_state\":{\"emergency_stopped\":true}}",
                out TelemetryResponse telemetry, out string error), Is.True, error);
            Assert.That(telemetry.control_state.emergency_stopped, Is.True);
        }

        [Test]
        public void Operators_ParseFromTheWrappedArray_AndTheNfcTagIsNeverModelled()
        {
            string wrapped = "{\"operators\":" +
                "[{\"operator_id\":\"op1\",\"name\":\"Ada\",\"role\":\"Inspector\",\"tag_uid\":\"04:A2:3B\"}]}";
            Assert.That(ApiJson.TryParse(wrapped, out OperatorsResponse response, out string error), Is.True, error);
            Assert.That(response.operators, Has.Length.EqualTo(1));
            Assert.That(response.operators[0].name, Is.EqualTo("Ada"));
            Assert.That(typeof(OperatorInfo).GetField("tag_uid"), Is.Null);
            Assert.That(ApiJson.TryParse("{\"operators\":[{\"operator_id\":\"\",\"name\":\"x\"}]}", out OperatorsResponse _, out _), Is.False);
        }

        [Test]
        public void MissionStatus_ParsesActiveAndInactive_WithNulls()
        {
            Assert.That(ApiJson.TryParse(
                "{\"active\":true,\"mission_id\":\"m1\",\"operator_id\":\"op1\",\"operator_name\":\"Ada\",\"zone\":\"Zone-A\",\"status\":\"active\",\"started_at\":\"t\",\"ended_at\":null}",
                out MissionStatus active, out string error), Is.True, error);
            Assert.That(active.active, Is.True);
            Assert.That(active.zone, Is.EqualTo("Zone-A"));
            Assert.That(ApiJson.TryParse("{\"active\":false,\"mission_id\":null}", out MissionStatus idle, out _), Is.True);
            Assert.That(idle.active, Is.False);
        }

        [Test]
        public void MissionRequests_SerializeTheFieldsTheBackendExpects()
        {
            Assert.That(UnityEngine.JsonUtility.ToJson(new MissionStartRequest("op1", "Zone-B")),
                Is.EqualTo("{\"operator_id\":\"op1\",\"zone\":\"Zone-B\"}"));
            Assert.That(UnityEngine.JsonUtility.ToJson(new MissionStopRequest("done")), Is.EqualTo("{\"reason\":\"done\"}"));
        }

        [Test]
        public void MissionRequiredResult_WithoutACommand_IsValid_AndRecognised()
        {
            Assert.That(ApiJson.TryParse(
                "{\"status\":\"MISSION_REQUIRED\",\"message\":\"An active mission is required for motion.\"}",
                out CommandResult result, out string error), Is.True, error);
            Assert.That(result.IsMissionRequired, Is.True);
            Assert.That(result.Accepted, Is.False);
            Assert.That(ApiJson.TryParse("{\"status\":\"accepted\"}", out CommandResult _, out _), Is.False);   // still strict
        }
    }
}
