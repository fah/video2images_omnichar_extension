"""Extract selected video frames as RGB image arrays."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

import numpy as np
from inline_core.errors import CancelledError
from inline_core.extensions.api import inline_node
from inline_core.graph.descriptor import Option, ParamField, Port, Widget
from inline_core.graph.runners import NodeResult, NodeRunner
from inline_core.graph.schema import PortKind
from inline_core.takes import AssetRef


_STRATEGIES = (
    Option("interval", "Every N seconds"),
    Option("timestamps", "Timestamp list (seconds)"),
    Option("frames", "Frame-number list (zero-based)"),
)


def parse_timestamps(value: str) -> list[float]:
    """Parse comma/semicolon-separated seconds or HH:MM:SS[.ms] timestamps."""
    parts = [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    if not parts:
        raise ValueError("Enter at least one timestamp, for example: 0, 1.5, 00:00:03.250")
    result: list[float] = []
    for part in parts:
        try:
            if ":" in part:
                fields = part.split(":")
                if len(fields) != 3:
                    raise ValueError
                hours, minutes, seconds = int(fields[0]), int(fields[1]), float(fields[2])
                if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
                    raise ValueError
                seconds = hours * 3600 + minutes * 60 + seconds
            else:
                seconds = float(part.removesuffix("s").strip())
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp {part!r}; use seconds or HH:MM:SS.mmm.") from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"Timestamp {part!r} must be a finite, non-negative time.")
        result.append(seconds)
    if result != sorted(result):
        raise ValueError("Timestamps must be in ascending order.")
    return result


def parse_frame_numbers(value: str) -> list[int]:
    """Parse comma/semicolon-separated, zero-based frame numbers."""
    parts = [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    if not parts:
        raise ValueError("Enter one or more zero-based frame numbers, for example: 0, 24, 48")
    try:
        numbers = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("Frame numbers must be whole, zero-based integers.") from exc
    if any(number < 0 for number in numbers):
        raise ValueError("Frame numbers must be zero or greater (0 is the first frame).")
    if numbers != sorted(numbers):
        raise ValueError("Frame numbers must be in ascending order.")
    return numbers


def select_nearest_frames(
    frames: Iterable[tuple[int, float, np.ndarray]], timestamps: list[float]
) -> list[np.ndarray]:
    """Select the nearest decoded frame per target timestamp, in one streaming pass."""
    if not timestamps:
        raise ValueError("At least one timestamp is required.")
    selected: list[np.ndarray] = []
    last_selected_index: int | None = None
    targets = iter(timestamps)
    target = next(targets, None)
    previous: tuple[int, float, np.ndarray] | None = None
    last_time = -1.0
    for current in frames:
        _index, timestamp, _image = current
        if timestamp < last_time:
            raise ValueError("Video presentation timestamps are not monotonic.")
        last_time = timestamp
        while target is not None and timestamp >= target:
            if previous is None or abs(previous[1] - target) > abs(timestamp - target):
                chosen = current
            else:
                chosen = previous
            if chosen[0] != last_selected_index:
                selected.append(chosen[2])
                last_selected_index = chosen[0]
            target = next(targets, None)
        previous = current
    if target is not None:
        raise ValueError(f"Requested timestamp {target:g}s is past the end of the video.")
    if not selected:
        raise ValueError("No frames were selected.")
    return selected


def _video_path(video: Any) -> str:
    if isinstance(video, AssetRef):
        path = video.path
    elif isinstance(video, (str, bytes)):
        path = video
    else:
        path = getattr(video, "path", None)
    if not path:
        raise ValueError("The connected video must resolve to a readable local file.")
    return str(path)


def _persist_frames(frames: list[np.ndarray], ctx: Any, node: Any, params: dict[str, Any]) -> list[AssetRef]:
    """Persist sampled images and pass downstream file references, never pixel arrays as paths."""
    if ctx.takes is None:
        raise ValueError("The video sampler needs an image take store to pass frames downstream.")
    refs: list[AssetRef] = []
    for index, image in enumerate(frames):
        take = ctx.takes.save(ctx.run_id, node.id, image, {**params, "sample_index": index})
        refs.append(AssetRef(ref="path", path=take.uri))
    return refs


@inline_node(
    type="fah/video2images",
    title="Video to Images",
    category="Image / Video",
    icon="image",
    inputs=(Port("video", "Video", PortKind.VIDEO, required=True),),
    outputs=(
        Port("frames", "Sampled frames", PortKind.IMAGE_LIST),
        Port("first_frame", "First sampled frame", PortKind.IMAGE),
    ),
    params=(
        ParamField("strategy", "Sampling method", Widget.SELECT, "interval", options=_STRATEGIES),
        ParamField("interval_seconds", "Interval (seconds)", Widget.NUMBER, 1.0, min=0.001, step=0.1),
        ParamField("timestamps", "Timestamps", Widget.TEXT, "0, 1, 2", advanced=True),
        ParamField("frame_numbers", "Frame numbers", Widget.TEXT, "0, 24, 48", advanced=True),
    ),
)
class VideoToImages(NodeRunner):
    """Sample a video and return disk-backed image references for downstream nodes."""

    produces_takes = False

    def run(self, node: Any, inputs: dict[str, list[Any]], ctx: Any) -> NodeResult:
        from av import open as av_open

        videos = inputs.get("video", [])
        if not videos:
            raise ValueError("Connect a video to the Video input.")
        params = {**VideoToImages.__inline_descriptor__.defaults(), **node.params}
        strategy = str(params["strategy"])
        source = _video_path(videos[0])
        selected: list[np.ndarray]

        with av_open(source, mode="r") as container:
            if not container.streams.video:
                raise ValueError("The selected file does not contain a video stream.")
            stream = container.streams.video[0]
            origin: float | None = None

            def decoded_frames() -> Iterable[tuple[int, float, np.ndarray]]:
                nonlocal origin
                for index, frame in enumerate(container.decode(stream)):
                    if ctx.cancel.cancelled:
                        raise CancelledError("Video frame sampling cancelled.")
                    if frame.pts is None or frame.time_base is None:
                        raise ValueError("A decoded frame has no presentation timestamp.")
                    absolute_time = float(frame.pts * frame.time_base)
                    if origin is None:
                        origin = absolute_time
                    yield index, absolute_time - origin, frame.to_ndarray(format="rgb24")

            if strategy in ("timestamps", "interval"):
                if strategy == "timestamps":
                    targets = parse_timestamps(str(params["timestamps"]))
                else:
                    interval = float(params["interval_seconds"])
                    if not math.isfinite(interval) or interval <= 0:
                        raise ValueError("Interval must be a finite number greater than zero.")
                    duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else 0.0
                    if duration <= 0 and container.duration:
                        duration = float(container.duration / 1_000_000)
                    if duration <= 0:
                        raise ValueError("Could not determine video duration for interval sampling.")
                    targets = [i * interval for i in range(math.floor(duration / interval) + 1)]
                selected = select_nearest_frames(decoded_frames(), targets)
            elif strategy == "frames":
                wanted = parse_frame_numbers(str(params["frame_numbers"]))
                wanted_set = set(wanted)
                selected = []
                frame_count = 0
                for index, _timestamp, image in decoded_frames():
                    frame_count = index + 1
                    if index in wanted_set:
                        selected.append(image)
                missing = [number for number in wanted if number >= frame_count]
                if missing:
                    raise ValueError(f"Frame number(s) {missing} are outside this video (0-based indexing).")
                if not selected:
                    raise ValueError("No frames were selected.")
            else:
                raise ValueError(f"Unknown sampling method: {strategy!r}.")

        refs = _persist_frames(selected, ctx, node, params)
        return NodeResult(outputs={"frames": refs, "first_frame": refs[0]})
