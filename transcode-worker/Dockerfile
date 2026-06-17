# GPU transcode worker — NVDEC/NVENC via ffmpeg.
# Same CUDA base as the OC worker so the host shares cached layers.
# h264_nvenc in the distro ffmpeg loads libnvidia-encode at runtime, provided
# by the NVIDIA container runtime (--gpus / device_requests).
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG CACHEBUST=1
COPY . .

ARG GIT_SHA=dev
ENV WORKER_CODE_VERSION=$GIT_SHA

CMD ["python", "-m", "worker.main"]
