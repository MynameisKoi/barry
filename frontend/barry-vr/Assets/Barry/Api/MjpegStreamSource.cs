using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace Barry.Api
{
    public sealed class MultipartJpegParser
    {
        private const int DefaultMaximumBufferBytes = 4 * 1024 * 1024;
        private byte[] buffer;
        private readonly int maximumBufferBytes;
        private int count;
        private int headerScanCursor;
        private int markerScanCursor;
        private int jpegStart = -1;
        private int pendingPayloadStart = -1;
        private int pendingContentLength;

        public MultipartJpegParser(int maximumBufferBytes = DefaultMaximumBufferBytes)
        {
            if (maximumBufferBytes < 4) throw new ArgumentOutOfRangeException(nameof(maximumBufferBytes));
            this.maximumBufferBytes = maximumBufferBytes;
            buffer = new byte[Math.Min(64 * 1024, maximumBufferBytes)];
        }

        public byte[] LatestFrame { get; private set; }

        public bool Append(byte[] data, int length)
        {
            if (data == null) throw new ArgumentNullException(nameof(data));
            if (length < 0 || length > data.Length) throw new ArgumentOutOfRangeException(nameof(length));
            if (length == 0) return false;
            if (count + length > maximumBufferBytes)
            {
                // An incomplete part larger than the cap is corrupt. Preserve only a
                // possible marker prefix so a marker-delimited stream can recover.
                byte trailing = count > 0 ? buffer[count - 1] : (byte)0;
                Reset();
                if (trailing == 0xff) buffer[count++] = trailing;
                if (count + length > maximumBufferBytes)
                {
                    int copyLength = maximumBufferBytes;
                    EnsureCapacity(copyLength);
                    Buffer.BlockCopy(data, length - copyLength, buffer, 0, copyLength);
                    count = copyLength;
                    return ExtractFrames();
                }
            }

            EnsureCapacity(count + length);
            Buffer.BlockCopy(data, 0, buffer, count, length);
            count += length;
            return ExtractFrames();
        }

        private bool ExtractFrames()
        {
            bool found = false;
            while (true)
            {
                if (pendingPayloadStart >= 0)
                {
                    if (count - pendingPayloadStart < pendingContentLength) return found;
                    LatestFrame = Copy(pendingPayloadStart, pendingContentLength);
                    Consume(pendingPayloadStart + pendingContentLength);
                    found = true;
                    continue;
                }

                int headerEnd = FindHeaderEnd();
                if (headerEnd >= 0)
                {
                    int payloadStart = headerEnd + 4;
                    if (TryReadContentLength(headerEnd, out int contentLength))
                    {
                        if (contentLength <= 0 || contentLength > maximumBufferBytes)
                        {
                            Reset();
                            return found;
                        }
                        pendingPayloadStart = payloadStart;
                        pendingContentLength = contentLength;
                        continue;
                    }

                    // Multipart without Content-Length is legal but less reliable. Only then
                    // fall back to markers, beginning after the MIME headers.
                    markerScanCursor = Math.Max(markerScanCursor, payloadStart);
                }

                if (!ExtractMarkerDelimitedFrame(out bool markerFound)) return found;
                found |= markerFound;
            }
        }

        private int FindHeaderEnd()
        {
            int start = Math.Max(0, headerScanCursor - 3);
            for (int i = start; i + 3 < count; i++)
            {
                if (buffer[i] == 13 && buffer[i + 1] == 10 && buffer[i + 2] == 13 && buffer[i + 3] == 10)
                {
                    headerScanCursor = i + 4;
                    return i;
                }
            }
            headerScanCursor = Math.Max(0, count - 3);
            return -1;
        }

        private bool TryReadContentLength(int headerEnd, out int contentLength)
        {
            string headers = Encoding.ASCII.GetString(buffer, 0, headerEnd);
            const string key = "Content-Length:";
            int keyIndex = headers.LastIndexOf(key, StringComparison.OrdinalIgnoreCase);
            if (keyIndex < 0)
            {
                contentLength = 0;
                return false;
            }
            int valueStart = keyIndex + key.Length;
            int valueEnd = headers.IndexOf('\r', valueStart);
            if (valueEnd < 0) valueEnd = headers.Length;
            return int.TryParse(headers.Substring(valueStart, valueEnd - valueStart).Trim(), out contentLength);
        }

        private bool ExtractMarkerDelimitedFrame(out bool found)
        {
            found = false;
            int start = Math.Max(0, markerScanCursor - 1);
            for (int i = start; i + 1 < count; i++)
            {
                if (jpegStart < 0)
                {
                    if (buffer[i] == 0xff && buffer[i + 1] == 0xd8)
                    {
                        jpegStart = i;
                        i++;
                    }
                    continue;
                }
                if (buffer[i] == 0xff && buffer[i + 1] == 0xd9)
                {
                    LatestFrame = Copy(jpegStart, i + 2 - jpegStart);
                    Consume(i + 2);
                    found = true;
                    return true;
                }
            }
            markerScanCursor = Math.Max(0, count - 1);
            return false;
        }

        private void EnsureCapacity(int required)
        {
            if (required <= buffer.Length) return;
            int capacity = buffer.Length;
            while (capacity < required) capacity = Math.Min(maximumBufferBytes, capacity * 2);
            Array.Resize(ref buffer, capacity);
        }

        private byte[] Copy(int offset, int length)
        {
            var result = new byte[length];
            Buffer.BlockCopy(buffer, offset, result, 0, length);
            return result;
        }

        private void Consume(int length)
        {
            int remaining = count - length;
            if (remaining > 0) Buffer.BlockCopy(buffer, length, buffer, 0, remaining);
            count = remaining;
            headerScanCursor = 0;
            markerScanCursor = 0;
            jpegStart = -1;
            pendingPayloadStart = -1;
            pendingContentLength = 0;
        }

        private void Reset()
        {
            count = 0;
            headerScanCursor = 0;
            markerScanCursor = 0;
            jpegStart = -1;
            pendingPayloadStart = -1;
            pendingContentLength = 0;
        }
    }

    public sealed class MjpegStreamSource : MonoBehaviour
    {
        public const float SnapshotPollIntervalSeconds = 0.2f;

        private readonly object frameLock = new object();
        private UnityWebRequest streamRequest;
        private UnityWebRequest snapshotRequest;
        private Coroutine snapshotCoroutine;
        private string snapshotUrl;
        private string cameraKey;
        private byte[] pendingFrame;
        private Texture2D texture;
        private float lastFrameTime;
        private int framesThisWindow;
        private float fpsWindowStart;

        public Texture2D Texture => texture;
        public float FramesPerSecond { get; private set; }
        public float FrameAgeSeconds => lastFrameTime <= 0 ? float.PositiveInfinity : Time.unscaledTime - lastFrameTime;
        public bool IsStreaming => streamRequest != null && !streamRequest.isDone;
        public bool IsUsingSnapshotFallback => snapshotCoroutine != null;
        public event Action<Texture2D> FrameReady;

        public void StartStream(string url, string cameraKey = null, string snapshotUrl = null)
        {
            if (string.IsNullOrWhiteSpace(url)) throw new ArgumentException("A stream URL is required.", nameof(url));
            StopStream();
            this.cameraKey = cameraKey;
            this.snapshotUrl = string.IsNullOrWhiteSpace(snapshotUrl) ? DeriveSnapshotUrl(url) : snapshotUrl;
            streamRequest = new UnityWebRequest(url, UnityWebRequest.kHttpVerbGET);
            streamRequest.downloadHandler = new LatestFrameDownloadHandler(QueueFrame);
            SetCameraKey(streamRequest);
            streamRequest.SendWebRequest();
            fpsWindowStart = Time.unscaledTime;
        }

        public void StopStream()
        {
            if (snapshotCoroutine != null)
            {
                StopCoroutine(snapshotCoroutine);
                snapshotCoroutine = null;
            }
            DisposeRequest(ref snapshotRequest);
            DisposeRequest(ref streamRequest);
            lock (frameLock) pendingFrame = null;
        }

        public static string DeriveSnapshotUrl(string streamUrl)
        {
            if (string.IsNullOrWhiteSpace(streamUrl)) throw new ArgumentException("A stream URL is required.", nameof(streamUrl));
            const string suffix = "/stream";
            int query = streamUrl.IndexOf('?');
            string path = query < 0 ? streamUrl : streamUrl.Substring(0, query);
            string queryString = query < 0 ? string.Empty : streamUrl.Substring(query);
            if (!path.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Cannot derive snapshot URL from a URL that does not end in /stream.", nameof(streamUrl));
            return path.Substring(0, path.Length - suffix.Length) + "/snapshot" + queryString;
        }

        private void Update()
        {
            // HANDOFF section 8: if streaming ends or fails, poll the snapshot
            // endpoint at approximately 5 Hz to preserve a degraded camera feed.
            if (streamRequest != null && streamRequest.isDone && snapshotCoroutine == null)
            {
                DisposeRequest(ref streamRequest, false);
                snapshotCoroutine = StartCoroutine(PollSnapshots());
            }

            byte[] frame;
            lock (frameLock)
            {
                frame = pendingFrame;
                pendingFrame = null;
            }
            if (frame == null) return;

            if (texture == null) texture = new Texture2D(2, 2, TextureFormat.RGB24, false);
            if (!texture.LoadImage(frame, false)) return;
            lastFrameTime = Time.unscaledTime;
            framesThisWindow++;
            float elapsed = lastFrameTime - fpsWindowStart;
            if (elapsed >= 1f)
            {
                FramesPerSecond = framesThisWindow / elapsed;
                framesThisWindow = 0;
                fpsWindowStart = lastFrameTime;
            }
            FrameReady?.Invoke(texture);
        }

        private IEnumerator PollSnapshots()
        {
            var wait = new WaitForSecondsRealtime(SnapshotPollIntervalSeconds);
            while (true)
            {
                snapshotRequest = UnityWebRequest.Get(snapshotUrl);
                SetCameraKey(snapshotRequest);
                yield return snapshotRequest.SendWebRequest();
                if (snapshotRequest.result == UnityWebRequest.Result.Success)
                {
                    byte[] bytes = snapshotRequest.downloadHandler.data;
                    if (bytes != null && bytes.Length > 0) QueueFrame(bytes);
                }
                DisposeRequest(ref snapshotRequest, false);
                yield return wait;
            }
        }

        private void QueueFrame(byte[] frame)
        {
            lock (frameLock) pendingFrame = frame;
        }

        private void SetCameraKey(UnityWebRequest request)
        {
            if (!string.IsNullOrEmpty(cameraKey)) request.SetRequestHeader("X-Camera-Key", cameraKey);
        }

        private static void DisposeRequest(ref UnityWebRequest request, bool abort = true)
        {
            if (request == null) return;
            if (abort && !request.isDone) request.Abort();
            request.Dispose();
            request = null;
        }

        private void OnDestroy()
        {
            StopStream();
            if (texture != null) Destroy(texture);
        }

        private sealed class LatestFrameDownloadHandler : DownloadHandlerScript
        {
            private readonly MultipartJpegParser parser = new MultipartJpegParser();
            private readonly Action<byte[]> frameReady;

            public LatestFrameDownloadHandler(Action<byte[]> frameReady) : base(new byte[64 * 1024])
            {
                this.frameReady = frameReady;
            }

            protected override bool ReceiveData(byte[] data, int dataLength)
            {
                if (data == null || dataLength <= 0) return false;
                if (parser.Append(data, dataLength)) frameReady(parser.LatestFrame);
                return true;
            }
        }
    }
}
