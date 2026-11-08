using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using Barry.Api;
using UnityEditor;
using UnityEngine;

namespace Barry.Tests.Smoke
{
    public static class ApiSmoke
    {
        private const double OverallTimeoutSeconds = 20.0;
        private static SmokeRun activeRun;

        public static void Run()
        {
            if (activeRun != null)
            {
                UnityEngine.Debug.LogError("API smoke test is already running.");
                EditorApplication.Exit(1);
                return;
            }

            string baseUrl = Environment.GetEnvironmentVariable("BARRY_BASE_URL");
            string robotKey = Environment.GetEnvironmentVariable("BARRY_ROBOT_KEY");
            if (string.IsNullOrWhiteSpace(baseUrl) || string.IsNullOrWhiteSpace(robotKey))
            {
                UnityEngine.Debug.LogError(
                    "API smoke requires BARRY_BASE_URL and BARRY_ROBOT_KEY environment variables.");
                EditorApplication.Exit(1);
                return;
            }

            var client = new BarryApiClient(baseUrl, robotKey) { TimeoutSeconds = 5 };
            activeRun = new SmokeRun(client, Finish);
            activeRun.Start();
        }

        private static void Finish(int exitCode)
        {
            activeRun = null;
            EditorApplication.Exit(exitCode);
        }

        private readonly struct StepResult
        {
            public readonly long HttpCode;
            public readonly bool ParsedOk;
            public readonly string Error;

            public StepResult(long httpCode, bool parsedOk, string error)
            {
                HttpCode = httpCode;
                ParsedOk = parsedOk;
                Error = error;
            }
        }

        private sealed class SmokeStep
        {
            public readonly string Method;
            public readonly string Path;
            public readonly Func<Action<StepResult>, IEnumerator> Start;

            public SmokeStep(
                string method,
                string path,
                Func<Action<StepResult>, IEnumerator> start)
            {
                Method = method;
                Path = path;
                Start = start;
            }
        }

        private sealed class SmokeRun
        {
            private readonly List<SmokeStep> steps;
            private readonly Action<int> finish;
            private readonly Stopwatch overall = new Stopwatch();
            private readonly Stopwatch stepTimer = new Stopwatch();
            private int stepIndex;
            private IEnumerator current;
            private AsyncOperation pendingOperation;
            private bool callbackReceived;
            private StepResult stepResult;
            private bool allSucceeded = true;
            private bool finished;

            public SmokeRun(BarryApiClient client, Action<int> finish)
            {
                this.finish = finish;
                steps = new List<SmokeStep>
                {
                    Step<StatusResponse>("GET", "/api/v1/status", client.GetStatus),
                    Step<TelemetryResponse>("GET", "/api/v1/telemetry", client.GetTelemetry),
                    Step<ActionsResponse>("GET", "/api/v1/actions", client.GetActions),
                    Step<SessionResponse>("POST", "/api/v1/session", client.AcquireSession,
                        session => !string.IsNullOrWhiteSpace(session.session_id)),
                    Step<CommandResult>("POST", "/api/v1/motion",
                        callback => client.SendMotion(new MotionRequest(0.2f, 0f, 0f), callback),
                        result => result.Accepted),
                    Step<CommandResult>("POST", "/api/v1/stop", client.Stop,
                        result => result.Accepted),
                    Step<StatusResponse>("GET", "/api/v1/status", client.GetStatus)
                };
            }

            public void Start()
            {
                overall.Start();
                EditorApplication.update += Tick;
            }

            private void Tick()
            {
                if (finished) return;
                try
                {
                    if (overall.Elapsed.TotalSeconds >= OverallTimeoutSeconds)
                    {
                        Timeout();
                        return;
                    }

                    if (current == null)
                    {
                        if (stepIndex >= steps.Count)
                        {
                            Complete(allSucceeded ? 0 : 1);
                            return;
                        }
                        BeginStep();
                    }

                    if (pendingOperation != null)
                    {
                        if (!pendingOperation.isDone) return;
                        pendingOperation = null;
                    }

                    bool hasNext = current.MoveNext();
                    if (hasNext)
                    {
                        pendingOperation = current.Current as AsyncOperation;
                        return;
                    }

                    EndStep();
                }
                catch (Exception exception)
                {
                    UnityEngine.Debug.LogException(exception);
                    Complete(1);
                }
            }

            private void BeginStep()
            {
                SmokeStep step = steps[stepIndex];
                callbackReceived = false;
                stepResult = default;
                stepTimer.Restart();
                current = step.Start(result =>
                {
                    callbackReceived = true;
                    stepResult = result;
                });
                if (current == null) throw new InvalidOperationException("Smoke step returned no coroutine.");
            }

            private void EndStep()
            {
                SmokeStep step = steps[stepIndex];
                stepTimer.Stop();
                if (!callbackReceived)
                    stepResult = new StepResult(0, false, "client callback was not invoked");

                allSucceeded &= stepResult.ParsedOk;
                Log(step, stepResult, stepTimer.ElapsedMilliseconds);
                stepIndex++;
                current = null;
                pendingOperation = null;
            }

            private void Timeout()
            {
                if (current != null && stepIndex < steps.Count)
                {
                    stepTimer.Stop();
                    Log(steps[stepIndex], new StepResult(0, false, "overall 20 s timeout"),
                        stepTimer.ElapsedMilliseconds);
                }
                UnityEngine.Debug.LogError("API smoke FAIL: overall 20 s timeout.");
                Complete(1);
            }

            private static void Log(SmokeStep step, StepResult result, long elapsedMilliseconds)
            {
                string outcome = result.ParsedOk ? "OK" : "FAIL";
                string suffix = string.IsNullOrWhiteSpace(result.Error) ? string.Empty : " error=" + result.Error;
                UnityEngine.Debug.Log(
                    $"{step.Method} {step.Path} HTTP {result.HttpCode} parsed {outcome} {elapsedMilliseconds}ms{suffix}");
            }

            private void Complete(int exitCode)
            {
                if (finished) return;
                finished = true;
                EditorApplication.update -= Tick;
                overall.Stop();
                UnityEngine.Debug.Log(exitCode == 0 ? "API smoke PASS" : "API smoke FAIL");
                finish(exitCode);
            }

            private static SmokeStep Step<T>(
                string method,
                string path,
                Func<Action<ApiResponse<T>>, IEnumerator> request,
                Func<T, bool> validate = null) where T : class
            {
                return new SmokeStep(method, path, done => request(response =>
                {
                    bool parsedOk = response.Success && response.Value != null &&
                                    (validate == null || validate(response.Value));
                    done(new StepResult(response.StatusCode, parsedOk, response.Error));
                }));
            }
        }
    }
}
