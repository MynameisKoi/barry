using System;
using UnityEngine;

namespace Barry.Api
{
    [Serializable]
    public sealed class MotionRequest
    {
        public float forward;
        public float strafe;
        public float turn;

        public MotionRequest(float forward, float strafe, float turn)
        {
            this.forward = forward;
            this.strafe = strafe;
            this.turn = turn;
        }
    }

    [Serializable]
    public sealed class MovementStyleRequest
    {
        public string intensity;
        public string gait;

        public MovementStyleRequest(string intensity, string gait = "trot")
        {
            this.intensity = intensity;
            this.gait = gait;
        }
    }

    [Serializable]
    public sealed class PostureRequest
    {
        public string height;
        public float roll;
        public float pitch;
        public float yaw;

        public PostureRequest(string height, float roll = 0f, float pitch = 0f, float yaw = 0f)
        {
            this.height = height;
            this.roll = roll;
            this.pitch = pitch;
            this.yaw = yaw;
        }
    }

    /// <summary>One entry of GET /api/operators. The NFC tag_uid is deliberately NOT modelled: the headset never needs it.</summary>
    [Serializable]
    public sealed class OperatorInfo
    {
        public string operator_id;
        public string name;
        public string role;
    }

    /// <summary>GET /api/operators returns a bare JSON array; BarryApiClient wraps it as {"operators":[...]}.</summary>
    [Serializable]
    public sealed class OperatorsResponse
    {
        public OperatorInfo[] operators;
    }

    [Serializable]
    public sealed class MissionStartRequest
    {
        public string operator_id;
        public string zone;

        public MissionStartRequest(string operatorId, string zone)
        {
            operator_id = operatorId;
            this.zone = zone;
        }
    }

    [Serializable]
    public sealed class MissionStopRequest
    {
        public string reason;

        public MissionStopRequest(string reason)
        {
            this.reason = reason;
        }
    }

    /// <summary>GET /api/mission/status, and the body of start/stop.</summary>
    [Serializable]
    public sealed class MissionStatus
    {
        public bool active;
        public string mission_id;
        public string operator_id;
        public string operator_name;
        public string zone;
        public string status;
        public string started_at;
        public string ended_at;
    }

    [Serializable]
    public sealed class CommandResult
    {
        public string status;
        public string command;
        public string message;
        public string intensity;

        public const string MissionRequiredStatus = "MISSION_REQUIRED";

        public bool Accepted => status == "accepted" || status == "stop_dispatched";

        /// <summary>The gateway refused motion because no mission is active (after #89, via the relay result).</summary>
        public bool IsMissionRequired => status == MissionRequiredStatus;
    }

    [Serializable]
    public sealed class ActionInfo
    {
        public int id;
        public string name;
    }

    [Serializable]
    public sealed class ActionsResponse
    {
        public ActionInfo[] actions;
    }

    [Serializable]
    public sealed class Orientation
    {
        public float roll;
        public float pitch;
        public float yaw;
    }

    [Serializable]
    public sealed class PostureState
    {
        public string height;
        public float roll;
        public float pitch;
        public float yaw;
    }

    [Serializable]
    public sealed class ControlState
    {
        public string intensity;
        public string gait;
        public PostureState posture;
        public bool moving;
        public bool emergency_stopped;
        public string last_command;
    }

    [Serializable]
    public sealed class TelemetryResponse
    {
        public float battery;
        public Orientation orientation;
        public string firmware;
        public string[] read_errors;
        public ControlState control_state;
    }

    [Serializable]
    public sealed class WatchdogStatus
    {
        public bool armed;
        public int timeout_ms;
    }

    [Serializable]
    public sealed class StatusResponse
    {
        public string robot_mode;
        public bool connected;
        public bool control_session_active;
        public int last_command_age_ms;
        public WatchdogStatus watchdog;
        public string intensity;
        public string gait;
    }

    [Serializable]
    public sealed class Resolution
    {
        public int width;
        public int height;
    }

    [Serializable]
    public sealed class CameraStatusResponse
    {
        public bool connected;
        public bool capturing;
        public bool streaming;
        public Resolution resolution;
        public float target_fps;
        public float actual_fps;
        public int active_viewers;
        public float last_frame_age_ms;
    }

    [Serializable]
    public sealed class SessionResponse
    {
        public string session_id;
        public float ttl_s;
    }

    [Serializable]
    internal sealed class ApiErrorBody
    {
        public string detail;
    }

    public static class ApiJson
    {
        public static bool TryParse<T>(string json, out T value, out string error) where T : class
        {
            value = null;
            error = null;
            if (string.IsNullOrWhiteSpace(json))
            {
                error = "Response body is empty.";
                return false;
            }

            try
            {
                value = JsonUtility.FromJson<T>(json);
            }
            catch (Exception exception)
            {
                error = "Invalid JSON: " + exception.Message;
                return false;
            }

            if (value == null)
            {
                error = "Response JSON did not contain an object.";
                return false;
            }

            error = Validate(value);
            return error == null;
        }

        private static string Validate(object value)
        {
            if (value is CommandResult result)
            {
                if (string.IsNullOrWhiteSpace(result.status)) return "Command result is missing status.";
                // Gateway refusals (e.g. MISSION_REQUIRED) carry a message but no command.
                if (string.IsNullOrWhiteSpace(result.command) && !result.IsMissionRequired)
                    return "Command result is missing command.";
            }
            else if (value is ActionsResponse actions)
            {
                if (actions.actions == null) return "Actions response is missing actions.";
                foreach (ActionInfo action in actions.actions)
                {
                    if (action == null || action.id <= 0 || string.IsNullOrWhiteSpace(action.name))
                        return "Actions response contains an invalid action.";
                }
            }
            else if (value is TelemetryResponse telemetry)
            {
                if (telemetry.orientation == null) return "Telemetry is missing orientation.";
                if (telemetry.control_state == null) return "Telemetry is missing control_state.";
                if (!IsFinite(telemetry.battery)) return "Telemetry battery is not finite.";
            }
            else if (value is StatusResponse status)
            {
                if (string.IsNullOrWhiteSpace(status.robot_mode)) return "Status is missing robot_mode.";
                if (status.watchdog == null || status.watchdog.timeout_ms < 0) return "Status has an invalid watchdog.";
            }
            else if (value is CameraStatusResponse camera)
            {
                if (camera.target_fps < 0 || camera.active_viewers < 0) return "Camera status contains negative values.";
                if (camera.resolution != null && (camera.resolution.width <= 0 || camera.resolution.height <= 0))
                    return "Camera status has an invalid resolution.";
            }
            else if (value is LlmStartResponse started)
            {
                // JsonUtility instantiates nested objects, so a missing task shows up as an empty state.
                if (started.task == null || string.IsNullOrWhiteSpace(started.task.state)) return "LLM start response is missing task.";
            }
            else if (value is LlmTaskStatus task)
            {
                if (string.IsNullOrWhiteSpace(task.state)) return "LLM task is missing state.";
            }
            else if (value is LlmCancelResponse cancelled)
            {
                if (cancelled.task == null || string.IsNullOrWhiteSpace(cancelled.task.state)) return "LLM cancel response is missing task.";
            }
            else if (value is OperatorsResponse operators)
            {
                if (operators.operators == null) return "Operators response is missing operators.";
                foreach (OperatorInfo op in operators.operators)
                {
                    if (op == null || string.IsNullOrWhiteSpace(op.operator_id) || string.IsNullOrWhiteSpace(op.name))
                        return "Operators response contains an invalid operator.";
                }
            }
            else if (value is SessionResponse session)
            {
                if (string.IsNullOrWhiteSpace(session.session_id) || session.ttl_s <= 0)
                    return "Session response is invalid.";
            }

            return null;
        }

        private static bool IsFinite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    }
}
