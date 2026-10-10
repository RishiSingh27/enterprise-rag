"""
Multimodal figure captioning (T028 / FR-013).

WHY THIS EXISTS
---------------
"Which quarter had the highest revenue?" cannot be answered from OCR of a bar
chart — OCR returns axis labels, not the chart's message. Sending the cropped
figure to a vision model returns a factual sentence that is then embedded like
any other text, making the chart retrievable with the same machinery.

Captions are cached by image hash (D-003), so re-ingestion costs nothing.
"""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

log = logging.getLogger("ekb.multimodal")

CAPTION_PROMPT = """Describe this figure factually for a search index.

Include, where visible:
- the figure type (bar chart, line graph, flow diagram, screenshot, photo)
- its title and axis labels
- the concrete data points, values and units
- the single clearest takeaway (highest, lowest, trend direction)

Rules: state only what is visible. Do not speculate or interpret beyond the
image. No preamble. Maximum 120 words."""

# Below this, a figure is almost always a logo, bullet icon or divider line.
MIN_FIGURE_PX = 100


class MultimodalDescriber:
    def __init__(self, settings):
        self.settings = settings
        self._cache: dict[str, str] = {}

    def describe_figure(self, file_path: Path, figure, page_number: int):
        """Returns ``(description, image_path)`` or None if skipped."""
        try:
            crop = self._crop(file_path, figure, page_number)
            if crop is None:
                return None

            image_bytes, image_path = crop
            image_hash = hashlib.sha256(image_bytes).hexdigest()

            if image_hash in self._cache:
                log.info("caption.cache_hit", extra={"image_hash": image_hash[:12]})
                return self._cache[image_hash], image_path

            description = self._call_vision(image_bytes)
            if not description:
                return None

            self._cache[image_hash] = description
            return description, image_path

        except Exception as exc:  # noqa: BLE001
            log.warning("caption.failed", extra={"page": page_number, "error": str(exc)[:200]})
            return None

    # ------------------------------------------------------------------ #

    def _crop(self, file_path: Path, figure, page_number: int):
        """T027. Crop the figure region from the page and save it for the UI."""
        import pdfplumber
        from PIL import Image

        regions = getattr(figure, "bounding_regions", None) or []
        if not regions:
            return None
        polygon = regions[0].polygon
        if not polygon or len(polygon) < 8:
            return None

        xs = polygon[0::2]
        ys = polygon[1::2]

        with pdfplumber.open(file_path) as pdf:
            if page_number > len(pdf.pages):
                return None
            page = pdf.pages[page_number - 1]
            scale = 150 / 72
            rendered = page.to_image(resolution=150).original

            # Document Intelligence reports inches; PDF points are 1/72 inch.
            box = (
                max(0, int(min(xs) * 72 * scale)),
                max(0, int(min(ys) * 72 * scale)),
                min(rendered.width, int(max(xs) * 72 * scale)),
                min(rendered.height, int(max(ys) * 72 * scale)),
            )
            if box[2] - box[0] < MIN_FIGURE_PX or box[3] - box[1] < MIN_FIGURE_PX:
                return None       # decorative, not informative

            cropped = rendered.crop(box)
            if cropped.mode != "RGB":
                cropped = cropped.convert("RGB")

            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()

            out_dir = self.settings.images_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{file_path.stem}_p{page_number}_{abs(hash(box)) % 99999}.png"
            out_path.write_bytes(image_bytes)

            return image_bytes, str(out_path)

    def _call_vision(self, image_bytes: bytes) -> str:
        import base64

        from ..core.azure_clients import get_openai_client

        b64 = base64.b64encode(image_bytes).decode()
        response = get_openai_client().chat.completions.create(
            model=self.settings.azure_chat_deployment,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": CAPTION_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "auto"}},
                ],
            }],
            max_tokens=300,
            temperature=0.1,
        )
        return (response.choices[0].message.content or "").strip()
