using System.Collections;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace Barry.VR
{
    public class BarryCameraReceiver : MonoBehaviour
    {
        [Header("Target UI")]
        public RawImage cameraDisplay;
        public Text statusText;

        [Header("Settings")]
        [Tooltip("Polling rate in frames per second for snapshot feed.")]
        public float targetFps = 15f;

        private Texture2D _videoTexture;
        private bool _isStreaming = true;

        private void Start()
        {
            _videoTexture = new Texture2D(2, 2, TextureFormat.RGB24, false);
            if (cameraDisplay != null)
            {
                cameraDisplay.texture = _videoTexture;
            }
            StartCoroutine(StreamPollingRoutine());
        }

        private void OnDestroy()
        {
            _isStreaming = false;
            if (_videoTexture != null)
            {
                Destroy(_videoTexture);
            }
        }

        private IEnumerator StreamPollingRoutine()
        {
            float delay = 1.0f / targetFps;

            while (_isStreaming)
            {
                if (BarryNetworkManager.Instance == null)
                {
                    yield return new WaitForSeconds(0.5f);
                    continue;
                }

                string url = $"{BarryNetworkManager.Instance.backendHttpUrl.TrimEnd('/')}/api/v1/camera/snapshot";
                using (UnityWebRequest req = UnityWebRequestTexture.GetTexture(url))
                {
                    req.SetRequestHeader("X-Camera-Key", BarryNetworkManager.Instance.cameraApiKey);
                    yield return req.SendWebRequest();

                    if (req.result == UnityWebRequest.Result.Success)
                    {
                        Texture2D downloadedTexture = DownloadHandlerTexture.GetContent(req);
                        if (cameraDisplay != null && downloadedTexture != null)
                        {
                            cameraDisplay.texture = downloadedTexture;
                            if (statusText != null)
                            {
                                statusText.text = "CAM: ONLINE (LIVE)";
                                statusText.color = Color.green;
                            }
                        }
                    }
                    else
                    {
                        if (statusText != null)
                        {
                            statusText.text = "CAM: RECONNECTING...";
                            statusText.color = Color.yellow;
                        }
                    }
                }

                yield return new WaitForSeconds(delay);
            }
        }
    }
}
