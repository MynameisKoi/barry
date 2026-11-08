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
    public sealed class CommandResult
    {
        public string status;
        public string command;
        public string message;
        public string intensity;

        public bool Accepted => status == "accepted" || status == "stop_dispatched";
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
                if (string.IsNullOrWhiteSpace(result.command)) return "Command result is missing command.";
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
