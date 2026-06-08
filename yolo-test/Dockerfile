FROM python:3.12-slim

WORKDIR /app

# ── NVDEC-capable ffmpeg ──────────────────────────────────────────────────────
# Frigate's image is already pulled on this host and was compiled with
# --enable-ffnvcodec --enable-cuda-llvm (hevc_cuvid, h264_cuvid, scale_cuda).
# Copying avoids a ~600MB extra layer vs a full CUDA base image.
COPY --from=ghcr.io/blakeblackshear/frigate:stable-tensorrt \
    /usr/lib/ffmpeg/7.0/bin/ffmpeg \
    /usr/local/bin/ffmpeg

# Runtime NVDEC libs (libnvcuvid.so, libcuda.so) come from nvidia-container-toolkit
# at run time via the 'runtime: nvidia' compose option — no extra apt packages needed.

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY test_track.py .

# Model will be bind-mounted from the OC worker model cache at runtime
# Videos will be bind-mounted from the ingest path

ENTRYPOINT ["python", "test_track.py"]
CMD ["--help"]
