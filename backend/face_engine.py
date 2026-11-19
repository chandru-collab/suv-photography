import os
import io
import base64
import urllib.request
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import cv2
import numpy as np
from PIL import Image, ImageOps

from backend.config import (
    YUNET_MODEL_PATH,
    SFACE_MODEL_PATH,
    YOLO_MODEL_PATH,
    YUNET_DOWNLOAD_URL,
    SFACE_DOWNLOAD_URL,
    YOLO_DOWNLOAD_URL,
    FACE_DETECTOR_SCORE_THRESH,
    FACE_DETECTOR_NMS_THRESH,
    YOLO_CONF_THRESH,
    YOLO_NMS_THRESH,
    USE_YOLO_DETECTOR,
    DEFAULT_MATCH_THRESHOLD,
    THUMBNAILS_DIR
)

logger = logging.getLogger("face_engine")

class FaceEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._ensure_models_exist()
        self.detector = None
        self.recognizer = None
        self.yolo_net = None
        self._load_models()

    def _ensure_models_exist(self):
        """Verify ONNX models exist locally; download if missing."""
        if not YUNET_MODEL_PATH.exists():
            logger.info(f"Downloading YuNet face detection ONNX model to {YUNET_MODEL_PATH}...")
            urllib.request.urlretrieve(YUNET_DOWNLOAD_URL, str(YUNET_MODEL_PATH))
            logger.info("YuNet download completed.")

        if not SFACE_MODEL_PATH.exists():
            logger.info(f"Downloading SFace face recognition ONNX model (38.7 MB) to {SFACE_MODEL_PATH}...")
            urllib.request.urlretrieve(SFACE_DOWNLOAD_URL, str(SFACE_MODEL_PATH))
            logger.info("SFace download completed.")

        if USE_YOLO_DETECTOR and not YOLO_MODEL_PATH.exists():
            logger.info(f"Downloading YOLOv8 Face ONNX model to {YOLO_MODEL_PATH}...")
            try:
                urllib.request.urlretrieve(YOLO_DOWNLOAD_URL, str(YOLO_MODEL_PATH))
                logger.info("YOLOv8 Face download completed.")
            except Exception as e:
                logger.warning(f"Could not download YOLO model: {e}")

    def _load_models(self):
        """Initialize OpenCV FaceDetectorYN, FaceRecognizerSF, and YOLO ONNX models."""
        try:
            self.detector = cv2.FaceDetectorYN.create(
                model=str(YUNET_MODEL_PATH),
                config="",
                input_size=(320, 320),
                score_threshold=FACE_DETECTOR_SCORE_THRESH,
                nms_threshold=FACE_DETECTOR_NMS_THRESH,
                top_k=5000
            )
            self.recognizer = cv2.FaceRecognizerSF.create(
                model=str(SFACE_MODEL_PATH),
                config=""
            )
            if USE_YOLO_DETECTOR and YOLO_MODEL_PATH.exists():
                try:
                    self.yolo_net = cv2.dnn.readNetFromONNX(str(YOLO_MODEL_PATH))
                    logger.info("YOLOv8 Face ONNX model successfully initialized.")
                except Exception as ye:
                    logger.warning(f"YOLOv8 Face initialization failed, running with YuNet: {ye}")
            logger.info("OpenCV YuNet & SFace ONNX models successfully initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize face engine: {e}")
            raise e

    def load_image_from_bytes(self, image_bytes: bytes, max_dimension: Optional[int] = 1600) -> Optional[np.ndarray]:
        """Load image bytes into BGR OpenCV format, respecting EXIF orientation and downsampling to max_dimension for memory safety."""
        try:
            pil_img = Image.open(io.BytesIO(image_bytes))
            # Automatically rotate based on EXIF tag from mobile cameras
            pil_img = ImageOps.exif_transpose(pil_img)
            # Memory safety: downsample large camera photos (e.g. 24-45MP DSLR) to max_dimension
            if max_dimension and (pil_img.width > max_dimension or pil_img.height > max_dimension):
                pil_img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            # Convert to RGB if needed
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            # Convert RGB to BGR for OpenCV
            rgb_arr = np.array(pil_img)
            bgr_arr = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)
            return bgr_arr
        except Exception as e:
            logger.warning(f"Unable to decode image from bytes: {e}")
            return None

    def load_image_from_path(self, filepath: str) -> Optional[np.ndarray]:
        """Load image from disk into BGR OpenCV format, respecting EXIF orientation."""
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            return self.load_image_from_bytes(data)
        except Exception as e:
            logger.warning(f"Unable to read image from path {filepath}: {e}")
            return None

    def create_thumbnail(self, image_bytes: bytes, output_path: str, max_size: int = 400) -> bool:
        """Create a lightweight web thumbnail for fast loading in galleries."""
        try:
            pil_img = Image.open(io.BytesIO(image_bytes))
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            pil_img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            pil_img.save(output_path, "JPEG", quality=85, optimize=True)
            return True
        except Exception as e:
            logger.warning(f"Failed to generate thumbnail {output_path}: {e}")
            return False

    def _detect_yolo(
        self,
        img_bgr: np.ndarray,
        conf_thresh: float = YOLO_CONF_THRESH,
        nms_thresh: float = YOLO_NMS_THRESH
    ) -> List[Tuple[int, int, int, int, float]]:
        """
        Run YOLOv8-Face feature pyramid inference with letterboxing.
        Returns list of (x, y, w, h, score).
        """
        if self.yolo_net is None or img_bgr is None or img_bgr.size == 0:
            return []

        with self._lock:
            h_orig, w_orig = img_bgr.shape[:2]
            r = min(640.0 / h_orig, 640.0 / w_orig)
            new_unpad = int(round(w_orig * r)), int(round(h_orig * r))
            dw, dh = (640.0 - new_unpad[0]) / 2.0, (640.0 - new_unpad[1]) / 2.0
            resized = cv2.resize(img_bgr, new_unpad, interpolation=cv2.INTER_LINEAR)
            top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
            left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
            canvas = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))

            blob = cv2.dnn.blobFromImage(canvas, 1.0 / 255.0, (640, 640), swapRB=True)
            self.yolo_net.setInput(blob)
            preds = self.yolo_net.forward()[0].T # (8400, 5)

            boxes = []
            scores = []
            for row in preds:
                score = float(row[4])
                if score > conf_thresh:
                    cx, cy, w, h = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                    orig_x = max(0, int((cx - dw - w / 2.0) / r))
                    orig_y = max(0, int((cy - dh - h / 2.0) / r))
                    orig_w = min(w_orig - orig_x, int(w / r))
                    orig_h = min(h_orig - orig_y, int(h / r))
                    if orig_w >= 18 and orig_h >= 18:
                        boxes.append([orig_x, orig_y, orig_w, orig_h])
                        scores.append(score)

            if not boxes:
                return []

            indices = cv2.dnn.NMSBoxes(boxes, scores, conf_thresh, nms_thresh)
            if len(indices) == 0:
                return []

            indices = indices.flatten()
            return [(boxes[i][0], boxes[i][1], boxes[i][2], boxes[i][3], scores[i]) for i in indices]

    def detect_and_extract(self, img_bgr: np.ndarray, multi_scale: bool = True) -> List[Dict[str, Any]]:
        """
        High-accuracy Dual-Engine face detection (YOLOv8-Face + YuNet) with landmark validation
        and 128-D SFace extraction.
        Combines YOLO's anchor-free context robustness for crowds, occlusions, and shadows
        with YuNet's landmark alignment and SFace's 128-D L2-normalized representations.
        Returns a list of dicts:
        [
            {
                "bbox": (x, y, w, h),
                "score": float,
                "embedding": np.ndarray(128,) normalized float32
            },
            ...
        ]
        """
        if img_bgr is None or img_bgr.size == 0:
            return []

        with self._lock:
            h, w = img_bgr.shape[:2]
            max_dim = max(h, w)

            # 1. Adaptive multi-scale YuNet evaluation
            scales = [1.0]
            if multi_scale:
                if max_dim > 2000:
                    scales = [1600.0 / max_dim, 1.0]
                elif max_dim < 600:
                    scales = [1.0, 1.4]
                elif max_dim > 1000:
                    scales = [1.0, 0.75]

            candidate_faces = []
            for s in scales:
                if s == 1.0:
                    scaled_img = img_bgr
                    sw, sh = w, h
                else:
                    sw, sh = int(w * s), int(h * s)
                    scaled_img = cv2.resize(
                        img_bgr,
                        (sw, sh),
                        interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
                    )

                self.detector.setInputSize((sw, sh))
                _, detected = self.detector.detect(scaled_img)
                if detected is not None:
                    for f in detected:
                        f_orig = f.copy()
                        if s != 1.0:
                            f_orig[0:14] /= s

                        bx, by, bw, bh = f_orig[0:4]
                        if bw < 18 or bh < 18 or (bx + bw) <= 0 or (by + bh) <= 0:
                            continue

                        # Anatomical keypoint check: minimum distance between eyes
                        re_x, re_y = f_orig[4], f_orig[5]
                        le_x, le_y = f_orig[6], f_orig[7]
                        eye_dist = float(np.hypot(re_x - le_x, re_y - le_y))
                        if eye_dist < 4.0:
                            continue

                        candidate_faces.append(f_orig)

            # 2. YOLOv8-Face complementary detection (catches shadowed, side, occluded faces)
            if self.yolo_net is not None:
                try:
                    yolo_detections = self._detect_yolo(img_bgr, conf_thresh=YOLO_CONF_THRESH)
                    for yx, yy, yw, yh, yscore in yolo_detections:
                        y_box = [yx, yy, yw, yh]
                        # Check if YuNet already detected this face
                        overlap = False
                        for cf in candidate_faces:
                            cb = [int(cf[0]), int(cf[1]), int(cf[2]), int(cf[3])]
                            x1 = max(y_box[0], cb[0])
                            y1 = max(y_box[1], cb[1])
                            x2 = min(y_box[0] + y_box[2], cb[0] + cb[2])
                            y2 = min(y_box[1] + y_box[3], cb[1] + cb[3])
                            inter = max(0, x2 - x1) * max(0, y2 - y1)
                            union = y_box[2] * y_box[3] + cb[2] * cb[3] - inter
                            iou = inter / union if union > 0 else 0
                            if iou > 0.40:
                                overlap = True
                                break

                        if not overlap:
                            f_yolo = np.zeros(15, dtype=np.float32)
                            f_yolo[0:4] = [yx, yy, yw, yh]
                            f_yolo[14] = yscore
                            candidate_faces.append(f_yolo)
                except Exception as ye:
                    logger.debug(f"YOLO detection auxiliary pass error: {ye}")

            if not candidate_faces:
                return []

            # Cross-model NMS to merge duplicate detections and pick the highest confidence
            boxes = [[int(f[0]), int(f[1]), int(f[2]), int(f[3])] for f in candidate_faces]
            scores = [float(f[14]) for f in candidate_faces]
            indices = cv2.dnn.NMSBoxes(boxes, scores, score_threshold=0.35, nms_threshold=0.38)
            if len(indices) == 0:
                return []

            indices = indices.flatten()
            selected_faces = [candidate_faces[i] for i in indices]

            results = []
            for face in selected_faces:
                score = float(face[14])
                bx, by, bw, bh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
                bbox = (float(bx), float(by), float(bw), float(bh))
                try:
                    # If YuNet 5-point landmarks exist, use alignCrop
                    if face[4] > 0 and face[6] > 0:
                        aligned_face = self.recognizer.alignCrop(img_bgr, face)
                    else:
                        # Direct crop with 15% margin for YOLO-only detected faces
                        mx = int(bw * 0.15)
                        my = int(bh * 0.15)
                        x1 = max(0, bx - mx)
                        y1 = max(0, by - my)
                        x2 = min(w, bx + bw + mx)
                        y2 = min(h, by + bh + my)
                        cropped = img_bgr[y1:y2, x1:x2]
                        if cropped.size > 0:
                            aligned_face = cv2.resize(cropped, (112, 112), interpolation=cv2.INTER_LINEAR)
                        else:
                            aligned_face = None

                    if aligned_face is not None and aligned_face.shape[0] == 112 and aligned_face.shape[1] == 112:
                        raw_feature = self.recognizer.feature(aligned_face) # shape (1, 128)
                        feat_1d = raw_feature.flatten().astype(np.float32)

                        # L2 normalize feature vector for exact dot-product cosine similarity
                        norm = np.linalg.norm(feat_1d)
                        if norm > 0:
                            normalized_feat = feat_1d / norm
                        else:
                            normalized_feat = feat_1d

                        results.append({
                            "bbox": bbox,
                            "score": score,
                            "embedding": normalized_feat
                        })
                except Exception as e:
                    logger.debug(f"alignCrop skipped noisy face: {e}")

            return results

    def extract_faces_with_crops(self, img_bgr: np.ndarray, max_faces: int = 6) -> List[Dict[str, Any]]:
        """
        Detect all faces in an image, extract 128-D embeddings, and generate a cropped
        JPEG base64 avatar for each face so the user can interactively select faces.
        Optimized with adaptive scale downsampling for sub-50ms selfie detection.
        """
        if img_bgr is None or img_bgr.size == 0:
            return []

        h_orig, w_orig = img_bgr.shape[:2]
        max_dim = max(h_orig, w_orig)
        if max_dim > 1280:
            scale = 1280.0 / max_dim
            img_bgr = cv2.resize(img_bgr, (int(w_orig * scale), int(h_orig * scale)), interpolation=cv2.INTER_AREA)

        # Fast single-scale pass for clear selfies
        raw_faces = self.detect_and_extract(img_bgr, multi_scale=False)
        if not raw_faces:
            # Fallback to multi-scale if no face found on fast pass
            raw_faces = self.detect_and_extract(img_bgr, multi_scale=True)
            if not raw_faces:
                return []

        # Sort largest face first
        raw_faces.sort(key=lambda f: f["bbox"][2] * f["bbox"][3], reverse=True)
        raw_faces = raw_faces[:max_faces]

        h_img, w_img = img_bgr.shape[:2]
        processed_faces = []

        for idx, f in enumerate(raw_faces):
            bx, by, bw, bh = f["bbox"]
            margin_x = int(bw * 0.2)
            margin_y = int(bh * 0.2)
            x1 = max(0, int(bx - margin_x))
            y1 = max(0, int(by - margin_y))
            x2 = min(w_img, int(bx + bw + margin_x))
            y2 = min(h_img, int(by + bh + margin_y))

            cropped = img_bgr[y1:y2, x1:x2]
            avatar_b64 = ""
            if cropped.size > 0:
                avatar_resized = cv2.resize(cropped, (120, 120), interpolation=cv2.INTER_AREA)
                success, encoded_jpg = cv2.imencode(".jpg", avatar_resized, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if success:
                    avatar_b64 = "data:image/jpeg;base64," + base64.b64encode(encoded_jpg.tobytes()).decode("utf-8")

            processed_faces.append({
                "index": idx,
                "bbox": f["bbox"],
                "score": round(float(f["score"]), 3),
                "embedding": f["embedding"],
                "avatar_b64": avatar_b64
            })

        return processed_faces

    def extract_single_face(self, img_bgr: np.ndarray) -> Tuple[Optional[np.ndarray], str]:
        """
        Used for guest query selfies. Returns (embedding, message).
        Enhanced with horizontal mirror test-time augmentation (TTA) to suppress
        asymmetric lighting noise and facial landmark jitter.
        """
        faces = self.detect_and_extract(img_bgr, multi_scale=True)
        if not faces:
            return None, "NO_FACE"

        # Pick the most prominent face by area * detector confidence score
        faces.sort(key=lambda f: f["bbox"][2] * f["bbox"][3] * f["score"], reverse=True)
        primary_emb = faces[0]["embedding"]
        status = "OK" if len(faces) == 1 else "MULTIPLE_FACES"

        # Test-Time Augmentation (TTA): extract embedding on mirrored selfie
        try:
            img_flip = cv2.flip(img_bgr, 1)
            faces_flip = self.detect_and_extract(img_flip, multi_scale=False)
            if faces_flip:
                faces_flip.sort(key=lambda f: f["bbox"][2] * f["bbox"][3] * f["score"], reverse=True)
                flip_emb = faces_flip[0]["embedding"]
                combined = (primary_emb + flip_emb) / 2.0
                norm = np.linalg.norm(combined)
                if norm > 0:
                    return (combined / norm), status
        except Exception:
            pass

        return primary_emb, status

    @staticmethod
    def match_query_vector(
        query_embedding: np.ndarray,
        candidate_embeddings: np.ndarray,
        threshold: float = DEFAULT_MATCH_THRESHOLD
    ) -> List[Tuple[int, float]]:
        """
        Vectorized cosine similarity comparison.
        query_embedding: shape (128,) unit vector
        candidate_embeddings: shape (N, 128) unit vectors
        Returns list of tuples: (row_index, similarity_score) sorted descending by score.
        """
        if candidate_embeddings.size == 0 or query_embedding is None:
            return []

        # Ensure query is 1D and unit normalized
        q = query_embedding.flatten()
        norm_q = np.linalg.norm(q)
        if norm_q > 0:
            q = q / norm_q

        # Dot product against all candidate rows
        sims = np.dot(candidate_embeddings, q)

        # Filter by threshold
        matched_indices = np.where(sims >= threshold)[0]
        results = [(int(idx), float(sims[idx])) for idx in matched_indices]

        # Sort highest similarity first
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    @staticmethod
    def match_multi_person(
        embeddings_list: List[np.ndarray],
        candidate_embeddings: np.ndarray,
        candidate_photo_ids: List[int],
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        mode: str = "together"
    ) -> List[Tuple[int, float]]:
        """
        Match multiple faces against candidate database.
        - mode='together': photos where ALL queried persons appear in the same image (Intersection).
        - mode='either': photos where ANY queried person appears (Union).
        Returns list of (photo_id, composite_score) sorted descending by score.
        """
        if candidate_embeddings.size == 0 or not embeddings_list or not candidate_photo_ids:
            return []

        person_photo_scores: List[Dict[int, float]] = []

        for q_emb in embeddings_list:
            if q_emb is None:
                continue
            q = q_emb.flatten()
            norm = np.linalg.norm(q)
            if norm > 0:
                q = q / norm

            sims = np.dot(candidate_embeddings, q)
            matched_indices = np.where(sims >= threshold)[0]

            photo_scores: Dict[int, float] = {}
            for idx in matched_indices:
                p_id = candidate_photo_ids[idx]
                score = float(sims[idx])
                if p_id not in photo_scores or score > photo_scores[p_id]:
                    photo_scores[p_id] = score

            person_photo_scores.append(photo_scores)

        if not person_photo_scores:
            return []

        if len(person_photo_scores) == 1:
            results = [(p_id, score) for p_id, score in person_photo_scores[0].items()]
            results.sort(key=lambda x: x[1], reverse=True)
            return results

        if mode == "together":
            # INTERSECTION: Photo must contain every person
            common_photo_ids = set.intersection(*(set(scores.keys()) for scores in person_photo_scores))
            results = []
            for p_id in common_photo_ids:
                avg_score = sum(scores[p_id] for scores in person_photo_scores) / len(person_photo_scores)
                results.append((p_id, avg_score))
            results.sort(key=lambda x: x[1], reverse=True)
            return results
        else:
            # UNION: Photo contains any person
            all_photo_ids = set.union(*(set(scores.keys()) for scores in person_photo_scores))
            results = []
            for p_id in all_photo_ids:
                max_score = max(scores.get(p_id, 0.0) for scores in person_photo_scores)
                results.append((p_id, max_score))
            results.sort(key=lambda x: x[1], reverse=True)
            return results

# Singleton instance
face_engine = FaceEngine()
