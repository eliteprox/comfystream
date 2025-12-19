ARG BASE_IMAGE=livepeer/comfyui-base:latest

FROM ${BASE_IMAGE}

ENV PATH="/workspace/miniconda3/bin:${PATH}"

# Install comfy-runner
COPY comfy_runner/ /workspace/comfy_runner/
# Install ai-runner and its deps with the base constraints, but keep aiohttp
# aligned with the ComfyUI version that is already present in the base image.
RUN conda run -n comfystream pip install \
    --constraint /workspace/comfystream/src/comfystream/scripts/constraints.txt \
    "ai-runner @ git+https://github.com/livepeer/ai-runner.git@c89d79fa88436539b00065abd5b5fcd54e459fe6#subdirectory=runner" \
    --no-deps && \
    conda run -n comfystream pip install \
    --constraint /workspace/comfystream/src/comfystream/scripts/constraints.txt \
    fastapi==0.115.6 \
    uvicorn==0.38.0 \
    prometheus_client==0.23.1 \
    Pillow==10.3.0 \
    numpy==1.26.4 \
    psutil==6.0.0 \
    nvidia-ml-py==12.560.30 \
    pynvml==12.0.0 \
    python-multipart>=0.0.21 \
    "aiohttp>=3.11.8" \
    pyzmq==26.2.0 \
    watchdog==5.0.2 \
    opencv-python==4.10.0.84 \
    av==14.4.0 \
    pytorch-triton==3.3.0
# Install comfy-runner itself without re-resolving dependencies (comfystream is in the base)
RUN conda run -n comfystream pip install \
    --constraint /workspace/comfystream/src/comfystream/scripts/constraints.txt \
    -e /workspace/comfy_runner/ \
    --no-deps

WORKDIR /workspace/comfy_runner

# Run ComfyRunner ai-runner worker
ENTRYPOINT ["conda", "run", "--no-capture-output", "-n", "comfystream", "python", "/workspace/comfy_runner/main.py"]

