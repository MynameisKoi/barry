using UnityEngine;

namespace Barry.VR
{
    public class BarryInputController : MonoBehaviour
    {
        [Header("Locomotion Parameters")]
        public float defaultSpeed = 0.3f;
        [Tooltip("Minimum time between consecutive locomotion commands in seconds")]
        public float commandInterval = 0.2f;

        private float _lastCommandTime = 0f;
        private string _lastSentAction = "";

        private void Update()
        {
            HandleKeyboardInput();
            HandleMetaXRInput();
        }

        private void HandleKeyboardInput()
        {
            if (BarryNetworkManager.Instance == null) return;

            // Emergency stop shortcut
            if (Input.GetKeyDown(KeyCode.Space) || Input.GetKeyDown(KeyCode.E))
            {
                BarryNetworkManager.Instance.TriggerEmergencyStop();
                return;
            }

            if (Input.GetKeyDown(KeyCode.R))
            {
                BarryNetworkManager.Instance.ResetEmergencyStop();
                return;
            }

            string currentAction = "";

            if (Input.GetKey(KeyCode.W) || Input.GetKey(KeyCode.UpArrow))
            {
                currentAction = "forward";
            }
            else if (Input.GetKey(KeyCode.S) || Input.GetKey(KeyCode.DownArrow))
            {
                currentAction = "backward";
            }
            else if (Input.GetKey(KeyCode.A) || Input.GetKey(KeyCode.LeftArrow))
            {
                currentAction = "left";
            }
            else if (Input.GetKey(KeyCode.D) || Input.GetKey(KeyCode.RightArrow))
            {
                currentAction = "right";
            }

            // If key was released and we were moving, send stop
            if (string.IsNullOrEmpty(currentAction) && !string.IsNullOrEmpty(_lastSentAction))
            {
                BarryNetworkManager.Instance.SendRobotCommand("stop", 0.0f);
                _lastSentAction = "";
                return;
            }

            // Throttle continuous key presses
            if (!string.IsNullOrEmpty(currentAction) && (Time.time - _lastCommandTime >= commandInterval))
            {
                BarryNetworkManager.Instance.SendRobotCommand(currentAction, defaultSpeed);
                _lastSentAction = currentAction;
                _lastCommandTime = Time.time;
            }
        }

        private void HandleMetaXRInput()
        {
            // Meta XR Controller mapping hooks:
            // When building on Meta Quest 3S, read PrimaryThumbstick / SecondaryThumbstick
            // e.g.: Vector2 primaryThumbstick = OVRInput.Get(OVRInput.Axis2D.PrimaryThumbstick);
            // Translate Y > 0.4 -> forward, Y < -0.4 -> backward, X < -0.4 -> left, X > 0.4 -> right
        }
    }
}
