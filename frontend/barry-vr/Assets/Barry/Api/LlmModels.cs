using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace Barry.Api
{
    [Serializable]
    public sealed class LlmGoalRequest
    {
        public string goal;

        public LlmGoalRequest(string goal)
        {
            this.goal = goal;
        }
    }

    /// <summary>One step of a brain task. The free-form args/result objects aren't parsed by JsonUtility;
    /// <see cref="LlmTaskParser"/> fills the two summary fields from the raw JSON instead.</summary>
    [Serializable]
    public sealed class LlmStep
    {
        public string tool;
        public string state;   // running | done | rejected | failed | skipped
        public string error;

        [NonSerialized] public string args_summary;     // e.g. "forward 2 s", "90°"
        [NonSerialized] public string result_status;    // e.g. "MISSION_REQUIRED" when the gateway refused the step
    }

    /// <summary>GET /api/v1/llm/task (or the task inside POST /plan and /task/cancel). <c>{"state":"idle"}</c> when none.</summary>
    [Serializable]
    public sealed class LlmTaskStatus
    {
        public const string Idle = "idle";
        public const string Running = "running";

        public int id;
        public string goal;
        public string state;     // idle | running | done | cancelled | failed | limit | refused
        public string message;   // why it ended (limit hit, error, refusal)
        public string answer;    // the model's final text
        public int turns;
        public LlmStep[] steps;

        public bool IsRunning => state == Running;
        public bool IsFinished => !string.IsNullOrEmpty(state) && state != Idle && state != Running;
    }

    [Serializable]
    public sealed class LlmStartResponse
    {
        public string status;   // "started"
        public LlmTaskStatus task;
    }

    [Serializable]
    public sealed class LlmCancelResponse
    {
        public bool cancelled;
        public LlmTaskStatus task;
    }

    /// <summary>Fills <see cref="LlmStep.args_summary"/> and <see cref="LlmStep.result_status"/> from the raw task JSON.</summary>
    public static class LlmTaskParser
    {
        private static readonly Regex ArgsObject = new Regex("\"args\"\\s*:\\s*\\{([^{}]*)\\}", RegexOptions.Compiled);
        private static readonly Regex ResultStatus = new Regex("\"result\"\\s*:\\s*\\{[^{}]*?\"status\"\\s*:\\s*\"([A-Za-z_]+)\"", RegexOptions.Compiled);

        public static void Enrich(string json, LlmTaskStatus task)
        {
            if (task?.steps == null || string.IsNullOrEmpty(json)) return;
            List<string> raw = SplitSteps(json);
            for (int i = 0; i < task.steps.Length && i < raw.Count; i++)
            {
                LlmStep step = task.steps[i];
                if (step == null) continue;
                step.args_summary = Summarize(step.tool, raw[i]);
                Match status = ResultStatus.Match(raw[i]);
                if (status.Success) step.result_status = status.Groups[1].Value;
            }
        }

        /// <summary>The top-level objects of the first "steps" array, as raw text. String-aware brace matching.</summary>
        public static List<string> SplitSteps(string json)
        {
            var steps = new List<string>();
            int key = json.IndexOf("\"steps\"", StringComparison.Ordinal);
            if (key < 0) return steps;
            int open = json.IndexOf('[', key);
            if (open < 0) return steps;

            int depth = 0, start = -1;
            bool inString = false, escaped = false;
            for (int i = open + 1; i < json.Length; i++)
            {
                char c = json[i];
                if (inString)
                {
                    if (escaped) escaped = false;
                    else if (c == '\\') escaped = true;
                    else if (c == '"') inString = false;
                    continue;
                }

                if (c == '"') inString = true;
                else if (c == '{') { if (depth++ == 0) start = i; }
                else if (c == '}') { if (--depth == 0 && start >= 0) { steps.Add(json.Substring(start, i - start + 1)); start = -1; } }
                else if (c == ']' && depth == 0) break;
            }
            return steps;
        }

        public static string Summarize(string tool, string rawStep)
        {
            Match m = ArgsObject.Match(rawStep ?? string.Empty);
            string args = m.Success ? m.Groups[1].Value : string.Empty;
            switch (tool)
            {
                case "move":
                    return Join(Value(args, "direction"), Number(args, "seconds", " s"));
                case "turn":
                    return Number(args, "degrees", "°");
                case "set_style":
                    return Join(Value(args, "intensity"), Value(args, "gait"));
                case "run_action":
                    return Join("action", Value(args, "action_id"));
                default:
                    return string.Empty;
            }
        }

        private static string Join(params string[] parts)
        {
            var sb = new StringBuilder();
            foreach (string p in parts)
            {
                if (string.IsNullOrEmpty(p)) continue;
                if (sb.Length > 0) sb.Append(' ');
                sb.Append(p);
            }
            return sb.ToString();
        }

        private static string Value(string args, string key)
        {
            Match m = Regex.Match(args, "\"" + key + "\"\\s*:\\s*(?:\"([^\"]*)\"|(-?[0-9.eE+-]+))");
            return m.Success ? (m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value) : string.Empty;
        }

        private static string Number(string args, string key, string suffix)
        {
            string text = Value(args, key);
            if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double value)) return string.Empty;
            return value.ToString("0.##", CultureInfo.InvariantCulture) + suffix;
        }
    }
}
