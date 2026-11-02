"""Check the tensor and shared-memory array boundary of live video output."""

import numpy as np
import pytest
import torch

from comfystream.pipeline import Pipeline


@pytest.mark.parametrize("tensor", [False, True])
@pytest.mark.parametrize("batched", [False, True])
def test_video_postprocess_preserves_rgb_and_clips(tensor, batched):
    pixels = np.broadcast_to(np.array([-0.2, 0.5, 1.2], dtype=np.float32), (8, 16, 3)).copy()
    if batched:
        pixels = pixels[None]
    if tensor:
        pixels = torch.from_numpy(pixels)
    frame = Pipeline.video_postprocess(None, pixels)
    result = frame.to_ndarray(format="rgb24")
    assert result.shape == (8, 16, 3)
    assert (result == [0, 127, 255]).all()


def test_video_postprocess_rejects_multiple_frames():
    with pytest.raises(ValueError, match="one RGB frame"):
        Pipeline.video_postprocess(None, np.zeros((2, 8, 16, 3)))
