"""YOLO11n ONNX detector with 640px letterboxing and class-aware NMS."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.models import DetectionAlert

COCO_CLASSES = ("person bicycle car motorcycle airplane bus train truck boat traffic light fire hydrant stop sign parking meter bench bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag tie suitcase frisbee skis snowboard sports ball kite baseball bat baseball glove skateboard surfboard tennis racket bottle wine glass cup fork knife spoon bowl banana apple sandwich orange broccoli carrot hot dog pizza donut cake chair couch potted plant bed dining table toilet tv laptop mouse remote keyboard cell phone microwave oven toaster sink refrigerator book clock vase scissors teddy bear hair drier toothbrush").split()


def non_max_suppression(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.45) -> list[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = np.maximum(0, x2-x1) * np.maximum(0, y2-y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0]); keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2-xx1) * np.maximum(0, yy2-yy1)
        union = areas[i] + areas[order[1:]] - inter
        iou = inter / np.maximum(union, 1e-9)
        order = order[1:][iou <= iou_threshold]
    return keep


class Detector:
    def __init__(self, model_path: str | None = None, confidence: float | None = None, session: Any = None):
        import os
        self.model_path = model_path or os.getenv("YOLO_MODEL_PATH", "models/yolo11n.onnx")
        self.confidence = float(confidence if confidence is not None else os.getenv("YOLO_CONF", "0.35"))
        self.session = session
        if self.session is None and Path(self.model_path).is_file():
            import onnxruntime as ort
            self.session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])

    @property
    def available(self) -> bool:
        return self.session is not None

    @staticmethod
    def letterbox(frame: np.ndarray, size: int = 640) -> tuple[np.ndarray, float, float, float]:
        h, w = frame.shape[:2]
        scale = min(size / w, size / h)
        nw, nh = int(round(w*scale)), int(round(h*scale))
        resized = cv2.resize(frame, (nw, nh))
        left, top = (size-nw)//2, (size-nh)//2
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[top:top+nh, left:left+nw] = resized
        tensor = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        return tensor[None], scale, float(left), float(top)

    def detect(self, frame: np.ndarray, zone: str = "Robot camera") -> list[DetectionAlert]:
        if self.session is None or frame is None:
            return []
        input_tensor, scale, pad_x, pad_y = self.letterbox(frame)
        output = self.session.run(None, {self.session.get_inputs()[0].name: input_tensor})[0]
        rows = np.asarray(output)[0]
        if rows.shape[0] == 84:
            rows = rows.T
        if rows.ndim != 2 or rows.shape[1] < 84:
            return []
        cls_scores = rows[:, 4:84]
        class_ids = cls_scores.argmax(axis=1)
        scores = cls_scores[np.arange(len(rows)), class_ids]
        selected = scores >= self.confidence
        rows, scores, class_ids = rows[selected], scores[selected], class_ids[selected]
        if not len(rows):
            return []
        xywh = rows[:, :4]
        boxes = np.column_stack((xywh[:,0]-xywh[:,2]/2, xywh[:,1]-xywh[:,3]/2,
                                 xywh[:,0]+xywh[:,2]/2, xywh[:,1]+xywh[:,3]/2))
        h, w = frame.shape[:2]
        results = []
        for cls in np.unique(class_ids):
            indices = np.where(class_ids == cls)[0]
            for idx in non_max_suppression(boxes[indices], scores[indices]):
                x1,y1,x2,y2 = boxes[indices[idx]]
                x1,x2 = np.clip((np.array([x1,x2])-pad_x)/scale, 0, w)
                y1,y2 = np.clip((np.array([y1,y2])-pad_y)/scale, 0, h)
                results.append(DetectionAlert(type="detection", object=COCO_CLASSES[int(cls)],
                    confidence=float(scores[indices[idx]]), zone=zone,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    bounding_box=[float(y1/h),float(x1/w),float(y2/h),float(x2/w)]))
        return results
