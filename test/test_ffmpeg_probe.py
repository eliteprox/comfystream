import json
import shutil
import subprocess

import pytest

from comfystream.ffmpeg.execute import FfmpegJobError, run_job


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg binaries required"
)
def test_metadata_probes_real_media_without_a_workflow_or_private_path(tmp_path):
    path = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=size=64x48:rate=4",
            "-t",
            "1",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
    )

    def no_workflow(_prompt):
        pytest.fail("Metadata probing must not queue an outputless workflow")

    result = run_job(no_workflow, str(tmp_path), "metadata", {"media": path.read_bytes()}, {})
    assert any(
        stream.get("width") == 64 and stream.get("height") == 48
        for stream in result["media"]["streams"]
    )
    assert float(result["media"]["format"]["duration"]) == 1
    assert "filename" not in result["media"]["format"]
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_invalid_media_probe_is_a_client_error(tmp_path):
    with pytest.raises(FfmpegJobError) as error:
        run_job(lambda _: None, str(tmp_path), "metadata", {"media": b"invalid media"}, {})
    assert error.value.status == 400
