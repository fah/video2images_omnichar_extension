"""Parser, timestamp-selection, and media-wire tests for the video sampler."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from inline_ext_video2images.sampler import (
    _persist_frames,
    parse_frame_numbers,
    parse_timestamps,
    select_nearest_frames,
)


def test_parse_timestamp_values_and_clock_format() -> None:
    assert parse_timestamps("0, 1.25s; 00:00:02.500") == [0.0, 1.25, 2.5]


@pytest.mark.parametrize("value", ["", "-1", "nan", "00:60:00", "1;;2", "2, 1"])
def test_parse_timestamps_rejects_bad_or_unsorted_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_timestamps(value)


def test_parse_frame_numbers_are_zero_based_integers() -> None:
    assert parse_frame_numbers("0, 24;48") == [0, 24, 48]
    with pytest.raises(ValueError):
        parse_frame_numbers("0, -1")


def test_nearest_timestamp_selection_is_ordered_and_deduplicated() -> None:
    frames = [
        (0, 0.0, np.full((1, 1, 3), 0, dtype=np.uint8)),
        (1, 0.8, np.full((1, 1, 3), 1, dtype=np.uint8)),
        (2, 1.7, np.full((1, 1, 3), 2, dtype=np.uint8)),
        (3, 3.0, np.full((1, 1, 3), 3, dtype=np.uint8)),
    ]
    result = select_nearest_frames(iter(frames), [0.0, 1.0, 2.8])
    assert [int(frame[0, 0, 0]) for frame in result] == [0, 1, 3]


def test_nearest_timestamp_selection_rejects_past_end() -> None:
    frames = [(0, 0.0, np.zeros((1, 1, 3), dtype=np.uint8))]
    with pytest.raises(ValueError, match="past the end"):
        select_nearest_frames(iter(frames), [2.0])


def test_persisted_outputs_are_path_refs_usable_by_encode_character() -> None:
    class Store:
        def __init__(self) -> None:
            self.saved: list[tuple[np.ndarray, dict[str, Any]]] = []

        def save(self, run_id: str, node_id: str, image: np.ndarray, params: dict[str, Any]) -> Any:
            self.saved.append((image, params))
            return type("Take", (), {"uri": f"/takes/frame-{len(self.saved)}.png"})()

    store = Store()
    ctx = type("Context", (), {"takes": store, "run_id": "run-1"})()
    node = type("Node", (), {"id": "sampler-1"})()
    images = [np.zeros((2, 2, 3), dtype=np.uint8), np.ones((2, 2, 3), dtype=np.uint8)]

    refs = _persist_frames(images, ctx, node, {"strategy": "frames"})

    assert [ref.path for ref in refs] == ["/takes/frame-1.png", "/takes/frame-2.png"]
    assert all(ref.ref == "path" for ref in refs)
    assert [params["sample_index"] for _, params in store.saved] == [0, 1]


def test_descriptor_matches_manifest_and_is_not_a_generating_take_node() -> None:
    import json
    from pathlib import Path

    from inline_ext_video2images.sampler import VideoToImages

    manifest: dict[str, Any] = json.loads(
        (Path(__file__).parent.parent / "inline-extension.json").read_text(encoding="utf-8")
    )
    assert {node["type"] for node in manifest["nodes"]} == {
        VideoToImages.__inline_descriptor__.type
    }
    assert VideoToImages.__inline_descriptor__.output_kind is None
    assert VideoToImages.produces_takes is False
