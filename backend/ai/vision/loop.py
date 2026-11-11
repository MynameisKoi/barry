"""Paced camera inference with stale-result protection."""
import os, threading, time

class DetectionLoop:
    def __init__(self, source, detector, fps=None, stale_s=2.0):
        self.source, self.detector = source, detector
        self.fps = max(.1, float(fps if fps is not None else os.getenv("YOLO_FPS", "3")))
        self.stale_s = stale_s
        self._stop = threading.Event(); self._lock = threading.Lock(); self._thread = None
        self._latest = []; self._updated_at = 0.0; self._available = False
    @property
    def available(self):
        return self._available and self.detector.available and time.monotonic()-self._updated_at <= self.stale_s
    def latest(self, zone="Robot camera"):
        if not self.available: return []
        with self._lock: return [d.model_copy(update={"zone": zone}) for d in self._latest]
    def start(self):
        if self.detector.available:
            self._stop.clear(); self._thread=threading.Thread(target=self._run,daemon=True); self._thread.start()
    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=2)
        try: self.source.close()
        except Exception: pass
    def _run(self):
        retry_delay = 0.5
        try:
            while not self._stop.is_set():
                try:
                    self.source.open()
                    self._available = True
                    retry_delay = 0.5
                    while not self._stop.is_set():
                        started = time.monotonic()
                        try:
                            detections = self.detector.detect(self.source.read())
                        except Exception:
                            self._available = False
                            with self._lock:
                                self._latest, self._updated_at = [], 0.0
                            self.source.close()
                            break
                        with self._lock:
                            self._latest, self._updated_at = detections, time.monotonic()
                        self._stop.wait(max(0, 1/self.fps-(time.monotonic()-started)))
                except Exception:
                    self._available = False
                    with self._lock:
                        self._latest, self._updated_at = [], 0.0
                    try:
                        self.source.close()
                    except Exception:
                        pass
                if not self._stop.is_set():
                    self._stop.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, 5.0)
        finally:
            self._available = False
            try:
                self.source.close()
            except Exception:
                pass
