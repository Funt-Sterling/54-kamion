"""Cheap, dependency-light image checks that run before anything gets sent to
the (paid, slower) vision model: resolution, brightness, blur, and a
perceptual hash for duplicate/near-duplicate detection.

Deliberately implemented with just Pillow + numpy (no OpenCV) to keep the
backend's install light for a hackathon; swap in OpenCV later if these
heuristics prove too coarse.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

MIN_WIDTH = 480
MIN_HEIGHT = 480
DARK_MEAN_THRESHOLD = 25.0  # 0-255 grayscale mean below this -> "too dark"
BLUR_VARIANCE_THRESHOLD = 15.0  # Laplacian variance below this -> "too blurry" (uncalibrated heuristic)
DUPLICATE_HAMMING_THRESHOLD = 4  # average-hash hamming distance <= this -> treat as a duplicate


@dataclass
class QualityResult:
    width: int
    height: int
    mean_brightness: float
    blur_score: float
    phash: str
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _laplacian_variance(gray: np.ndarray) -> float:
    """Variance of a 3x3 Laplacian response — a standard cheap blur proxy.
    Implemented by hand to avoid an OpenCV/SciPy dependency."""
    padded = np.pad(gray, 1, mode="edge").astype(np.float32)
    lap = (
        padded[:-2, 1:-1]
        + padded[1:-1, :-2]
        - 4 * padded[1:-1, 1:-1]
        + padded[1:-1, 2:]
        + padded[2:, 1:-1]
    )
    return float(lap.var())


def _average_hash(gray_img: Image.Image) -> str:
    small = gray_img.resize((8, 8), Image.LANCZOS)
    pixels = np.asarray(small, dtype=np.float32)
    mean = pixels.mean()
    bits = (pixels > mean).flatten()
    return "".join("1" if b else "0" for b in bits)


def hamming_distance(hash_a: str, hash_b: str) -> int:
    return sum(a != b for a, b in zip(hash_a, hash_b))


def is_duplicate(phash: str, existing_hashes: list[str]) -> bool:
    return any(hamming_distance(phash, h) <= DUPLICATE_HAMMING_THRESHOLD for h in existing_hashes)


def assess_image(path: str | Path) -> QualityResult:
    img = Image.open(path).convert("RGB")
    width, height = img.size
    gray_img = img.convert("L")
    gray = np.asarray(gray_img, dtype=np.float32)

    mean_brightness = float(gray.mean())
    blur_score = _laplacian_variance(gray)
    phash = _average_hash(gray_img)

    issues: list[str] = []
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        issues.append("resolution_too_low")
    if mean_brightness < DARK_MEAN_THRESHOLD:
        issues.append("too_dark")
    if blur_score < BLUR_VARIANCE_THRESHOLD:
        issues.append("too_blurry")

    return QualityResult(
        width=width,
        height=height,
        mean_brightness=mean_brightness,
        blur_score=blur_score,
        phash=phash,
        issues=issues,
    )
