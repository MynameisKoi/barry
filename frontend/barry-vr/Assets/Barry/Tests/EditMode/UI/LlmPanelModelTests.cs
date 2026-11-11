using System;
using System.Collections;
using Barry.Api;
using Barry.Teleop;
using NUnit.Framework;

namespace Barry.UI.Tests
{
    public sealed class LlmPanelModelTests
    {
        private sealed class StubApi : ILlmApi
        {
            public IEnumerator StartLlmTask(string g, Action<ApiResponse<LlmStartResponse>> c) { yield break; }
            public IEnumerator GetLlmTask(Action<ApiResponse<LlmTaskStatus>> c) { yield break; }
            public IEnumerator CancelLlmTask(Action<ApiResponse<LlmCancelResponse>> c) { yield break; }
        }

        private static LlmTaskMonitor MonitorWith(LlmTaskStatus task)
        {
            var api = new CapturingApi();
            var monitor = new LlmTaskMonitor(api, _ => { });
            monitor.Start("go");
            api.StartCb(new ApiResponse<LlmStartResponse>(true, 200, new LlmStartResponse { status = "started", task = task }, null));
            return monitor;
        }

        private sealed class CapturingApi : ILlmApi
        {
            public Action<ApiResponse<LlmStartResponse>> StartCb;
            public IEnumerator StartLlmTask(string g, Action<ApiResponse<LlmStartResponse>> c) { StartCb = c; return Empty(); }
            public IEnumerator GetLlmTask(Action<ApiResponse<LlmTaskStatus>> c) { return Empty(); }
            public IEnumerator CancelLlmTask(Action<ApiResponse<LlmCancelResponse>> c) { return Empty(); }
            private static IEnumerator Empty() { yield break; }
        }

        private static LlmStep Step(string tool, string state, string args = "", string result = null) =>
            new LlmStep { tool = tool, state = state, args_summary = args, result_status = result };

        [Test]
        public void Toggle_OpensAndClosesThePanel()
        {
            var model = new LlmPanelModel();
            Assert.That(model.Open, Is.False);
            model.Toggle();
            Assert.That(model.Open, Is.True);
            model.Toggle();
            Assert.That(model.Open, Is.False);
        }

        [Test]
        public void Idle_ShowsPresetsEnabled_NoCancel_NoAnswer()
        {
            var model = new LlmPanelModel();
            model.Update(new LlmTaskMonitor(new StubApi(), _ => { }));
            Assert.That(model.Chip.Text, Is.EqualTo("IDLE"));
            Assert.That(model.PresetsEnabled, Is.True);
            Assert.That(model.CancelVisible, Is.False);
            Assert.That(model.AnswerText, Is.Empty);
            Assert.That(model.Rows, Is.Empty);
        }

        [Test]
        public void Running_ShowsTurnsAndLiveSteps_AndACancelButton()
        {
            var model = new LlmPanelModel();
            model.Update(MonitorWith(new LlmTaskStatus
            {
                id = 1, state = "running", turns = 2,
                steps = new[] { Step("get_detections", "done"), Step("move", "running", "forward 2 s") }
            }));

            Assert.That(model.Chip.Text, Is.EqualTo("RUNNING · TURN 2"));
            Assert.That(model.Chip.Tone, Is.EqualTo(StatusTone.Accent));
            Assert.That(model.CancelVisible, Is.True);
            Assert.That(model.CancelEnabled, Is.True);
            Assert.That(model.Rows, Has.Count.EqualTo(2));
            Assert.That(model.Rows[0].Text, Is.EqualTo("1. get_detections"));
            Assert.That(model.Rows[0].StateWord, Is.EqualTo("done"));
            Assert.That(model.Rows[1].Text, Is.EqualTo("2. move forward 2 s"));
            Assert.That(model.Rows[1].StateWord, Is.EqualTo("running…"));
            Assert.That(model.Rows[1].Tone, Is.EqualTo(StatusTone.Accent));
            Assert.That(model.Notice, Is.EqualTo(LlmPanelModel.ReplacesNotice));
            Assert.That(model.PresetsEnabled, Is.True);              // a new task may be started: it replaces this one
        }

        [Test]
        public void Done_ShowsTheAnswer_WithAWordAndOkTone_AndNoCancel()
        {
            var model = new LlmPanelModel();
            model.Update(MonitorWith(new LlmTaskStatus { id = 1, state = "done", turns = 2, answer = "I see: chair.", steps = new LlmStep[0] }));
            Assert.That(model.Chip.Text, Is.EqualTo("DONE"));
            Assert.That(model.Chip.Tone, Is.EqualTo(StatusTone.Ok));
            Assert.That(model.AnswerText, Is.EqualTo("I see: chair."));
            Assert.That(model.AnswerTone, Is.EqualTo(StatusTone.Ok));
            Assert.That(model.CancelVisible, Is.False);
            Assert.That(model.Notice, Is.Empty);
        }

        [TestCase("cancelled", "CANCELLED", StatusTone.Warning, "Stopped by hand input", "Stopped by hand input")]
        [TestCase("cancelled", "CANCELLED", StatusTone.Warning, null, "The task was cancelled.")]
        [TestCase("limit", "LIMIT REACHED", StatusTone.Warning, "step limit of 12 reached", "step limit of 12 reached")]
        [TestCase("failed", "FAILED", StatusTone.Danger, "a robot step failed", "a robot step failed")]
        [TestCase("refused", "REFUSED", StatusTone.Danger, null, "The assistant declined this request.")]
        public void EndedWithoutAnAnswer_ShowsTheMessageInWordsAndTone(string state, string chip, StatusTone tone, string message, string shown)
        {
            var model = new LlmPanelModel();
            model.Update(MonitorWith(new LlmTaskStatus { id = 1, state = state, message = message, turns = 1, steps = new LlmStep[0] }));
            Assert.That(model.Chip.Text, Is.EqualTo(chip));
            Assert.That(model.Chip.Tone, Is.EqualTo(tone));
            Assert.That(model.AnswerText, Is.EqualTo(shown));
            Assert.That(model.AnswerTone, Is.EqualTo(tone));
            Assert.That(model.CancelVisible, Is.False);
        }

        [Test]
        public void ADoneTaskWithNoText_StillSaysDone()
        {
            var model = new LlmPanelModel();
            model.Update(MonitorWith(new LlmTaskStatus { id = 1, state = "done", answer = " ", steps = new LlmStep[0] }));
            Assert.That(model.AnswerText, Is.EqualTo("Done."));
        }

        [Test]
        public void Steps_ShowRefusalsAsTheirStatusWord_AndNormalSuccessesAsDone()
        {
            var model = new LlmPanelModel();
            model.Update(MonitorWith(new LlmTaskStatus
            {
                id = 1, state = "done", turns = 2, steps = new[]
                {
                    Step("move", "done", "forward 2 s", "MISSION_REQUIRED"),
                    Step("turn", "done", "90°", "ACCEPTED"),
                    Step("move", "done", "back 1 s", "STOP_DISPATCHED"),
                    Step("set_style", "rejected", "low walk"),
                    Step("run_action", "failed", "action 10"),
                    Step("stop", "skipped")
                }
            }));
            Assert.That(model.Rows[0].StateWord, Is.EqualTo("MISSION_REQUIRED"));
            Assert.That(model.Rows[0].Tone, Is.EqualTo(StatusTone.Warning));
            Assert.That(model.Rows[1].StateWord, Is.EqualTo("done"));
            Assert.That(model.Rows[1].Tone, Is.EqualTo(StatusTone.Ok));
            Assert.That(model.Rows[2].StateWord, Is.EqualTo("done"));
            Assert.That(model.Rows[3].StateWord, Is.EqualTo("rejected"));
            Assert.That(model.Rows[4].Tone, Is.EqualTo(StatusTone.Danger));
            Assert.That(model.Rows[5].StateWord, Is.EqualTo("skipped"));
        }

        [Test]
        public void ManySteps_KeepTheNewestInView_AndCountTheRest()
        {
            var steps = new LlmStep[9];
            for (int i = 0; i < steps.Length; i++) steps[i] = Step("get_status", "done");
            var model = new LlmPanelModel();
            model.Update(MonitorWith(new LlmTaskStatus { id = 1, state = "running", turns = 4, steps = steps }));
            Assert.That(model.Rows, Has.Count.EqualTo(LlmPanelModel.MaxRows));
            Assert.That(model.HiddenSteps, Is.EqualTo(3));
            Assert.That(model.Rows[0].Text, Is.EqualTo("4. get_status"));     // numbering continues from the hidden ones
            Assert.That(model.Rows[LlmPanelModel.MaxRows - 1].Text, Is.EqualTo("9. get_status"));
        }

        [Test]
        public void StartingAndCancelling_DisableTheRightControls()
        {
            var api = new CapturingApi();
            var monitor = new LlmTaskMonitor(api, _ => { });
            var model = new LlmPanelModel();

            monitor.Start("go");
            model.Update(monitor);
            Assert.That(model.Chip.Text, Is.EqualTo("STARTING…"));
            Assert.That(model.PresetsEnabled, Is.False);

            api.StartCb(new ApiResponse<LlmStartResponse>(true, 200, new LlmStartResponse { task = new LlmTaskStatus { id = 1, state = "running", steps = new LlmStep[0] } }, null));
            monitor.Cancel();
            model.Update(monitor);
            Assert.That(model.CancelText, Is.EqualTo("Cancelling…"));
            Assert.That(model.CancelEnabled, Is.False);
        }

        [Test]
        public void AnApiError_IsShownAsTheAnswerWithADangerTone()
        {
            var api = new CapturingApi();
            var monitor = new LlmTaskMonitor(api, _ => { });
            monitor.Start("go");
            api.StartCb(new ApiResponse<LlmStartResponse>(false, 503, null, "LLM control is not configured"));
            var model = new LlmPanelModel();
            model.Update(monitor);
            Assert.That(model.AnswerText, Is.EqualTo("LLM control is not configured"));
            Assert.That(model.AnswerTone, Is.EqualTo(StatusTone.Danger));
        }

        [Test]
        public void ThePresets_AreOneEditableList_WithLabelsAndGoals()
        {
            Assert.That(LlmPresets.All, Has.Length.GreaterThanOrEqualTo(4));
            foreach (LlmPreset preset in LlmPresets.All)
            {
                Assert.That(preset.Label, Is.Not.Empty);
                Assert.That(preset.Goal, Is.Not.Empty);
            }
            Assert.That(LlmPresets.All[0].Goal, Does.Contain("see"));
        }
    }
}
