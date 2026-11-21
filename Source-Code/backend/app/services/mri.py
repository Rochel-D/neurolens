"""Brain MRI module (neuro only) — deep module.

Public interface:
    analyse(data: bytes, filename: str) -> MriResult

Hidden inside: upload validation, "does this look like an MRI" guard, EfficientNet-B0 inference
(4 classes from the public Kaggle Brain Tumor MRI dataset), Grad-CAM heatmap.
If the trained weights are missing, it says so honestly instead of guessing.
"""
from __future__ import annotations

import base64
import io
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from ..config import ROOT, get_settings

CLASSES = ["glioma", "meningioma", "notumor", "pituitary"]
LABELS = {"glioma": "Glioma pattern", "meningioma": "Meningioma pattern", "notumor": "No tumour pattern", "pituitary": "Pituitary tumour pattern"}
IMG_SIZE = 224
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class MriError(ValueError):
    pass


@dataclass
class MriResult:
    available: bool
    label: str = ""
    display: str = ""
    confidence: float = 0.0
    probabilities: dict = field(default_factory=dict)
    heatmap_png: str = ""
    image_png: str = ""
    message: str = ""

    def dict(self) -> dict:
        return asdict(self)


def load_image(data: bytes, max_mb: int = 10) -> Image.Image:
    if len(data) > max_mb * 1024 * 1024:
        raise MriError(f"Image is larger than {max_mb} MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError) as e:
        raise MriError("Please upload a JPG or PNG image of a brain MRI slice") from e
    if img.width < 64 or img.height < 64:
        raise MriError("Image is too small to analyse")
    return img.convert("RGB")


def looks_like_mri(img: Image.Image) -> tuple[bool, float]:
    """Guardrail: brain MRI slices are greyscale. Colourful photos are rejected."""
    hsv = np.asarray(img.convert("HSV").resize((128, 128)), dtype=np.float32)
    saturation = float(hsv[..., 1].mean() / 255.0)
    return saturation < 0.18, saturation


class _Model:
    _lock = threading.Lock()
    model = None
    error = ""

    @classmethod
    def get(cls):
        if cls.model is not None or cls.error:
            return cls.model
        with cls._lock:
            if cls.model is None and not cls.error:
                path = Path(get_settings().mri_model_path)
                if not path.is_absolute():
                    path = ROOT / path
                if not path.exists():
                    cls.error = "model file models/mri_classifier.pt not found"
                    return None
                try:
                    import torch
                    from torchvision.models import efficientnet_b0

                    m = efficientnet_b0(weights=None)
                    m.classifier[1] = torch.nn.Linear(m.classifier[1].in_features, len(CLASSES))
                    state = torch.load(path, map_location="cpu", weights_only=True)
                    m.load_state_dict(state.get("model", state))
                    cls.model = m.eval()
                except ImportError:
                    cls.error = "PyTorch not installed (pip install -r requirements-ml.txt)"
                except Exception as e:  # noqa: BLE001
                    cls.error = f"{type(e).__name__}: {e}"[:200]
        return cls.model


def model_status() -> dict:
    _Model.get()
    return {"loaded": _Model.model is not None, "error": _Model.error or None, "classes": CLASSES,
            "path": get_settings().mri_model_path}


def _preprocess(img: Image.Image):
    import torch

    arr = np.asarray(img.resize((IMG_SIZE, IMG_SIZE)), dtype=np.float32) / 255.0
    arr = (arr - MEAN) / STD
    return torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0)


def _gradcam(model, x, class_idx: int) -> np.ndarray:
    """Grad-CAM on the last convolutional block of EfficientNet-B0."""
    import torch

    acts, grads = {}, {}
    layer = model.features[-1]
    h1 = layer.register_forward_hook(lambda m, i, o: acts.__setitem__("a", o))
    h2 = layer.register_full_backward_hook(lambda m, gi, go: grads.__setitem__("g", go[0]))
    try:
        x = x.clone().requires_grad_(True)
        out = model(x)
        model.zero_grad()
        out[0, class_idx].backward()
        a, g = acts["a"][0], grads["g"][0]
        weights = g.mean(dim=(1, 2))
        cam = torch.relu((weights[:, None, None] * a).sum(0)).detach().numpy()
    finally:
        h1.remove()
        h2.remove()
    cam = cam - cam.min()
    return cam / (cam.max() + 1e-8)


def _overlay(img: Image.Image, cam: np.ndarray) -> str:
    base = img.resize((IMG_SIZE, IMG_SIZE))
    heat = Image.fromarray((cam * 255).astype(np.uint8)).resize((IMG_SIZE, IMG_SIZE), Image.BILINEAR)
    h = np.asarray(heat, dtype=np.float32) / 255.0
    # simple "jet-like" colour map without matplotlib
    rgb = np.stack([np.clip(1.5 - abs(4 * h - 3), 0, 1), np.clip(1.5 - abs(4 * h - 2), 0, 1), np.clip(1.5 - abs(4 * h - 1), 0, 1)], -1)
    blended = (0.55 * np.asarray(base, dtype=np.float32) / 255 + 0.45 * rgb) * 255
    buf = io.BytesIO()
    Image.fromarray(blended.clip(0, 255).astype(np.uint8)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _small_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.resize((IMG_SIZE, IMG_SIZE)).convert("L").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def analyse(data: bytes, filename: str = "") -> MriResult:
    s = get_settings()
    img = load_image(data, s.max_upload_mb)
    ok, sat = looks_like_mri(img)
    if not ok:
        raise MriError("This image looks like a colour photo, not a brain MRI slice. Please upload a greyscale MRI image.")
    model = _Model.get()
    if model is None:
        return MriResult(
            available=False,
            image_png=_small_png(img),
            message=(
                "The MRI classifier is not installed on this server yet, so no prediction was made. "
                "(Admin: train it with training/mri/train_mri_classifier.py and put mri_classifier.pt in the models/ folder.)"
            ),
        )
    import torch

    x = _preprocess(img)
    with torch.no_grad():
        probs = torch.softmax(model(x), dim=1)[0].numpy()
    idx = int(probs.argmax())
    cam = _gradcam(model, x, idx)
    label = CLASSES[idx]
    return MriResult(
        available=True,
        label=label,
        display=LABELS[label],
        confidence=float(probs[idx]),
        probabilities={c: round(float(p), 4) for c, p in zip(CLASSES, probs)},
        heatmap_png=_overlay(img, cam),
        image_png=_small_png(img),
        message="" if probs[idx] >= s.mri_min_confidence else "Low confidence — the model is not sure about this image.",
    )
