using Barry.Api;
using NUnit.Framework;

namespace Barry.Api.Tests
{
    public sealed class LlmModelsTests
    {
        private const string DoneTask =
            "{\"id\":3,\"goal\":\"Look and tell me what you see\",\"state\":\"done\",\"message\":null,\"answer\":\"I see: chair.\",\"turns\":2," +
            "\"steps\":[" +
            "{\"tool\":\"get_detections\",\"args\":{},\"state\":\"done\",\"result\":[{\"label\":\"chair\",\"confidence\":0.88,\"age_s\":0.1}],\"error\":null}," +
            "{\"tool\":\"move\",\"args\":{\"direction\":\"forward\",\"seconds\":2.0,\"speed\":0.3},\"state\":\"done\",\"result\":{\"status\":\"MISSION_REQUIRED\",\"message\":\"An active mission is required for motion.\"},\"error\":null}," +
            "{\"tool\":\"turn\",\"args\":{\"degrees\":-90.0},\"state\":\"done\",\"result\":{\"status\":\"ACCEPTED\",\"message\":\"ok\",\"details\":{\"clamped\":true}},\"error\":null}," +
            "{\"tool\":\"set_style\",\"args\":{\"intensity\":\"low\",\"gait\":\"walk\"},\"state\":\"rejected\",\"result\":null,\"error\":\"bad\"}," +
            "{\"tool\":\"run_action\",\"args\":{\"action_id\":10},\"state\":\"skipped\",\"result\":null,\"error\":null}" +
            "]}";

        private static LlmTaskStatus Parse(string json)
        {
            Assert.That(ApiJson.TryParse(json, out LlmTaskStatus task, out string error), Is.True, error);
            LlmTaskParser.Enrich(json, task);
            return task;
        }

        [Test]
        public void DoneTask_ParsesTheDocumentedShape()
        {
            LlmTaskStatus task = Parse(DoneTask);
            Assert.That(task.id, Is.EqualTo(3));
            Assert.That(task.state, Is.EqualTo("done"));
            Assert.That(task.answer, Is.EqualTo("I see: chair."));
            Assert.That(task.turns, Is.EqualTo(2));
            Assert.That(task.steps, Has.Length.EqualTo(5));
            Assert.That(task.IsRunning, Is.False);
            Assert.That(task.IsFinished, Is.True);
            Assert.That(task.steps[0].tool, Is.EqualTo("get_detections"));
            Assert.That(task.steps[3].state, Is.EqualTo("rejected"));
            Assert.That(task.steps[3].error, Is.EqualTo("bad"));
        }

        [Test]
        public void StepArgs_AreSummarisedForEveryTool()
        {
            LlmTaskStatus task = Parse(DoneTask);
            Assert.That(task.steps[0].args_summary, Is.Empty);
            Assert.That(task.steps[1].args_summary, Is.EqualTo("forward 2 s"));
            Assert.That(task.steps[2].args_summary, Is.EqualTo("-90°"));
            Assert.That(task.steps[3].args_summary, Is.EqualTo("low walk"));
            Assert.That(task.steps[4].args_summary, Is.EqualTo("action 10"));
        }

        [Test]
        public void GatewayRefusalsAndSuccesses_AreExtractedFromEachStepsResult_OnlyForObjectResults()
        {
            LlmTaskStatus task = Parse(DoneTask);
            Assert.That(task.steps[0].result_status, Is.Null);                    // a detection list, not an object
            Assert.That(task.steps[1].result_status, Is.EqualTo("MISSION_REQUIRED"));
            Assert.That(task.steps[2].result_status, Is.EqualTo("ACCEPTED"));     // not leaked from a nested object
            Assert.That(task.steps[3].result_status, Is.Null);
        }

        [Test]
        public void ArgsInOneStepNeverLeakIntoAnother()
        {
            LlmTaskStatus task = Parse(
                "{\"id\":1,\"state\":\"running\",\"turns\":1,\"steps\":[" +
                "{\"tool\":\"move\",\"args\":{\"direction\":\"back\",\"seconds\":1.5,\"speed\":0.2},\"state\":\"done\",\"result\":{\"ok\":true}}," +
                "{\"tool\":\"turn\",\"args\":{\"degrees\":45.5},\"state\":\"running\",\"result\":null}]}");
            Assert.That(task.steps[0].args_summary, Is.EqualTo("back 1.5 s"));
            Assert.That(task.steps[1].args_summary, Is.EqualTo("45.5°"));
        }

        [Test]
        public void StringsContainingBracesOrQuotes_DoNotBreakStepSplitting()
        {
            string json = "{\"id\":1,\"state\":\"done\",\"turns\":1,\"answer\":\"a } ] \\\" tricky\",\"steps\":[" +
                "{\"tool\":\"move\",\"args\":{\"direction\":\"forward\",\"seconds\":1,\"speed\":0.3},\"state\":\"done\",\"result\":{\"status\":\"ACCEPTED\",\"message\":\"x } y\"},\"error\":null}," +
                "{\"tool\":\"turn\",\"args\":{\"degrees\":10},\"state\":\"done\",\"result\":null,\"error\":null}]}";
            LlmTaskStatus task = Parse(json);
            Assert.That(task.steps, Has.Length.EqualTo(2));
            Assert.That(task.steps[1].args_summary, Is.EqualTo("10°"));
            Assert.That(LlmTaskParser.SplitSteps(json), Has.Count.EqualTo(2));
        }

        [TestCase("{\"state\":\"idle\"}", "idle", false)]
        [TestCase("{\"id\":1,\"state\":\"running\",\"turns\":1,\"steps\":[]}", "running", true)]
        public void IdleAndRunning_Parse(string json, string state, bool running)
        {
            LlmTaskStatus task = Parse(json);
            Assert.That(task.state, Is.EqualTo(state));
            Assert.That(task.IsRunning, Is.EqualTo(running));
            Assert.That(task.IsFinished, Is.False);
        }

        [TestCase("cancelled", "Cancelled by the operator")]
        [TestCase("failed", "a robot step failed")]
        [TestCase("refused", "The assistant declined this request.")]
        [TestCase("limit", "step limit of 12 reached")]
        public void ErrorStates_ParseWithTheirMessage(string state, string message)
        {
            LlmTaskStatus task = Parse("{\"id\":2,\"goal\":\"x\",\"state\":\"" + state + "\",\"message\":\"" + message + "\",\"answer\":null,\"turns\":3,\"steps\":[]}");
            Assert.That(task.state, Is.EqualTo(state));
            Assert.That(task.message, Is.EqualTo(message));
            Assert.That(task.IsFinished, Is.True);
        }

        [Test]
        public void StartAndCancelResponses_WrapTheTask()
        {
            string started = "{\"status\":\"started\",\"task\":" + DoneTask + "}";
            Assert.That(ApiJson.TryParse(started, out LlmStartResponse start, out string e1), Is.True, e1);
            LlmTaskParser.Enrich(started, start.task);
            Assert.That(start.status, Is.EqualTo("started"));
            Assert.That(start.task.steps[1].args_summary, Is.EqualTo("forward 2 s"));

            string cancelled = "{\"cancelled\":true,\"task\":{\"id\":3,\"state\":\"cancelled\",\"turns\":1,\"steps\":[]}}";
            Assert.That(ApiJson.TryParse(cancelled, out LlmCancelResponse cancel, out string e2), Is.True, e2);
            Assert.That(cancel.cancelled, Is.True);
            Assert.That(cancel.task.state, Is.EqualTo("cancelled"));
        }

        [Test]
        public void MalformedResponses_AreRejected()
        {
            Assert.That(ApiJson.TryParse("{\"id\":1}", out LlmTaskStatus _, out _), Is.False);                 // no state
            Assert.That(ApiJson.TryParse("{\"status\":\"started\"}", out LlmStartResponse _, out _), Is.False);  // no task
            Assert.That(ApiJson.TryParse("{\"cancelled\":true}", out LlmCancelResponse _, out _), Is.False);
        }

        [Test]
        public void GoalRequest_SerializesTheFieldTheBackendExpects()
        {
            Assert.That(UnityEngine.JsonUtility.ToJson(new LlmGoalRequest("Walk forward 2 seconds")),
                Is.EqualTo("{\"goal\":\"Walk forward 2 seconds\"}"));
        }
    }
}
