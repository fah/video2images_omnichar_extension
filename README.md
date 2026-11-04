# Video to Images for OmniChar

An Inline Studio extension for extracting image frames from a connected video. It provides one node with three sampling methods:

- **Every N seconds** — sample at regular time intervals, starting at 0 seconds.
- **Timestamp list** — enter comma- or semicolon-separated seconds (`0, 1.5, 4`) or timestamps (`00:00:01.500`). Each requested time selects the nearest decoded presentation-timestamp frame.
- **Frame-number list** — enter zero-based decoded frame numbers (`0, 24, 48`). Frame 0 is the first frame.

The node returns a `Sampled frames` `IMAGE_LIST` for downstream nodes and `First sampled frame` as an image preview. No video or model files are written. Video is decoded locally with PyAV (`av`), using the version already installed by the OmniChar runtime; the extension intentionally does not pin or replace this host dependency.

## Install

In OmniChar, open **Extensions → Available** and install `https://github.com/fah/video2images_omnichar_extension`, or use **Install from URL**.

## Development and tests

```bash
pip install -e /path/to/OmniChar/core
pip install pytest
PYTHONPATH=python pytest
```

Frame numbers are zero-based. Timestamp values must be non-negative and in ascending order. Interval sampling requires a readable video duration. If PyAV is not installed in the host, install OmniChar with its video/runtime extra rather than adding a second `av` version to this extension.

Inspired by the `UC_SampleVideoFramesAsImages` node in [ComfyUI-UtilsCollection](https://github.com/silveroxides/ComfyUI-UtilsCollection).
