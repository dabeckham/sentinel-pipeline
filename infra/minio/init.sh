#!/bin/sh
# MinIO bucket initialization — runs once at stack startup via minio-init service

set -e

echo "Waiting for MinIO to be ready..."
until mc alias set sentinel http://${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY} 2>/dev/null; do
  echo "MinIO not ready yet, retrying in 2s..."
  sleep 2
done

echo "Creating buckets..."
mc mb --ignore-existing sentinel/${MINIO_BUCKET_FRAMES}
mc mb --ignore-existing sentinel/${MINIO_BUCKET_CROPS}
mc mb --ignore-existing sentinel/${MINIO_BUCKET_SNAPSHOTS}

echo "Setting bucket policies (private)..."
mc anonymous set none sentinel/${MINIO_BUCKET_FRAMES}
mc anonymous set none sentinel/${MINIO_BUCKET_CROPS}
mc anonymous set none sentinel/${MINIO_BUCKET_SNAPSHOTS}

echo "MinIO initialization complete."
mc ls sentinel/
