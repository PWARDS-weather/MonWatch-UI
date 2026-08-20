import os
from pathlib import Path
import tifffile as tiff
import numpy as np
from PIL import Image

class CacheManager:
    """
    Generates a pyramidal cache of PNG images from input TIFFs.
    """
    # Zoom levels for generating cached images
    ZOOM_LEVELS = [1.0, 0.5, 0.25]

    def __init__(self, input_dir: str, cache_dir: str):
        self.input_dir = Path(input_dir)
        self.cache_dir = Path(cache_dir)

        if not self.input_dir.exists():
            raise FileNotFoundError(f"[ERROR] Input directory not found: {self.input_dir}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def generate_pyramidal_cache(self):
        """
        Reads all TIFF files in the input directory and generates
        resized PNG images at predefined zoom levels.
        """
        # Collect TIFF files (case-insensitive)
        tiff_files = [p for p in self.input_dir.iterdir() if p.suffix.lower() == ".tif"]
        if not tiff_files:
            raise FileNotFoundError(f"[ERROR] No TIFF found in {self.input_dir}")

        for tif_path in tiff_files:
            print(f"[INFO] Processing {tif_path.name}")
            image = tiff.imread(tif_path)

            # Handle planar configuration
            if image.ndim == 3 and image.shape[0] in (3, 4):
                image = np.moveaxis(image, 0, -1)

            # Normalize to uint8 if necessary
            if image.dtype != np.uint8:
                image = ((image - image.min()) / image.ptp() * 255).astype(np.uint8)

            pil_image = Image.fromarray(image)

            # Generate resized PNGs for each zoom level
            for z in self.ZOOM_LEVELS:
                width = int(pil_image.width * z)
                height = int(pil_image.height * z)
                resized = pil_image.resize((width, height), Image.LANCZOS)
                out_path = self.cache_dir / f"{tif_path.stem}_x{z}.png"
                resized.save(out_path)
                print(f"[INFO] Saved cached image: {out_path}")

# If used as a standalone script
if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    input_dir = base_dir / "test"
    cache_dir = base_dir / "cache" / "images"

    manager = CacheManager(str(input_dir), str(cache_dir))
    manager.generate_pyramidal_cache()
