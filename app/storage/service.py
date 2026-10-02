"""
LeadAI Pluggable Media Storage Service (Phase 5).

Supports:
- Local filesystem storage (default for standalone deployments)
- AWS S3 (and S3-compatible services like MinIO, Cloudflare R2, Backblaze B2)
- Cloudinary (CDN-optimized image storage)

Selection is controlled via the STORAGE_BACKEND environment variable:
  STORAGE_BACKEND="local" | "s3" | "cloudinary" (default: "local")
"""
import abc
import hashlib
import logging
import os
import time
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

# Base static directory for local storage
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


class BaseStorageProvider(abc.ABC):
    """Abstract base class for media storage providers."""

    @abc.abstractmethod
    async def upload_file(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        folder: str = "media",
    ) -> str:
        """Upload a file and return its accessible URL."""
        raise NotImplementedError

    @abc.abstractmethod
    async def delete_file(self, file_url_or_path: str) -> bool:
        """Delete a file by its URL or relative path."""
        raise NotImplementedError


class LocalStorageProvider(BaseStorageProvider):
    """Local filesystem storage provider serving files under /static/<folder>/."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or STATIC_DIR

    async def upload_file(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        folder: str = "media",
    ) -> str:
        target_dir = os.path.join(self.base_dir, folder)
        os.makedirs(target_dir, exist_ok=True)
        file_path = os.path.join(target_dir, filename)

        with open(file_path, "wb") as f:  # noqa: ASYNC230
            f.write(content)

        return f"/static/{folder}/{filename}"

    async def delete_file(self, file_url_or_path: str) -> bool:
        clean = file_url_or_path.replace("/static/", "").lstrip("/\\")
        full_path = os.path.join(self.base_dir, clean)
        if os.path.isfile(full_path):
            try:
                os.remove(full_path)
                return True
            except OSError as e:
                logger.warning("Failed to delete local file %s: %s", full_path, e)
                return False
        return False


class S3StorageProvider(BaseStorageProvider):
    """AWS S3 and S3-compatible (MinIO, Cloudflare R2) storage provider."""

    def __init__(
        self,
        bucket: str | None = None,
        region: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        endpoint_url: str | None = None,
        custom_domain: str | None = None,
    ):
        self.bucket = bucket or os.environ.get("AWS_S3_BUCKET") or os.environ.get("S3_BUCKET_NAME") or "leadai-media"
        self.region = region or os.environ.get("AWS_REGION") or "us-east-1"
        self.access_key = access_key or os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret_key = secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY")
        self.endpoint_url = endpoint_url or os.environ.get("AWS_S3_ENDPOINT_URL")
        self.custom_domain = custom_domain or os.environ.get("AWS_S3_CUSTOM_DOMAIN")

    async def upload_file(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        folder: str = "media",
    ) -> str:
        key = f"{folder}/{filename}".lstrip("/")

        # If boto3 is available, use it for native IAM / credential management
        try:
            import boto3
            client_kw = {"region_name": self.region}
            if self.endpoint_url:
                client_kw["endpoint_url"] = self.endpoint_url
            if self.access_key and self.secret_key:
                client_kw["aws_access_key_id"] = self.access_key
                client_kw["aws_secret_access_key"] = self.secret_key

            s3 = boto3.client("s3", **client_kw)
            s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )
        except ImportError:
            # Fallback to direct HTTP PUT with signature or simulation
            if not self.access_key or not self.secret_key:
                logger.warning("S3 credentials not configured; falling back to local file storage simulation")
                return await LocalStorageProvider().upload_file(content, filename, content_type, folder)

            # AWS SigV4 implementation or endpoint PUT
            base_host = self.endpoint_url or f"https://{self.bucket}.s3.{self.region}.amazonaws.com"
            async with httpx.AsyncClient() as client:
                resp = await client.put(
                    f"{base_host}/{key}",
                    content=content,
                    headers={"Content-Type": content_type},
                    timeout=30.0,
                )
                resp.raise_for_status()

        if self.custom_domain:
            domain = self.custom_domain.replace("https://", "").replace("http://", "").strip("/")
            return f"https://{domain}/{key}"
        if self.endpoint_url:
            return f"{self.endpoint_url.rstrip('/')}/{self.bucket}/{key}"
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}"

    async def delete_file(self, file_url_or_path: str) -> bool:
        if "://" in file_url_or_path:
            parsed = urllib.parse.urlparse(file_url_or_path)
            key = parsed.path.lstrip("/")
            if self.bucket and key.startswith(f"{self.bucket}/"):
                key = key[len(self.bucket) + 1 :]
        else:
            key = file_url_or_path.lstrip("/")

        try:
            import boto3
            client_kw = {"region_name": self.region}
            if self.endpoint_url:
                client_kw["endpoint_url"] = self.endpoint_url
            if self.access_key and self.secret_key:
                client_kw["aws_access_key_id"] = self.access_key
                client_kw["aws_secret_access_key"] = self.secret_key
            s3 = boto3.client("s3", **client_kw)
            s3.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to delete S3 object %s: %s", key, e)
            return False


class CloudinaryStorageProvider(BaseStorageProvider):
    """Cloudinary storage provider using Cloudinary's direct REST upload API."""

    def __init__(
        self,
        cloud_name: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        raw_url = os.environ.get("CLOUDINARY_URL")
        if raw_url and raw_url.startswith("cloudinary://"):
            parsed = urllib.parse.urlparse(raw_url)
            self.api_key = parsed.username or ""
            self.api_secret = parsed.password or ""
            self.cloud_name = parsed.hostname or ""
        else:
            self.cloud_name = cloud_name or os.environ.get("CLOUDINARY_CLOUD_NAME") or ""
            self.api_key = api_key or os.environ.get("CLOUDINARY_API_KEY") or ""
            self.api_secret = api_secret or os.environ.get("CLOUDINARY_API_SECRET") or ""

    async def upload_file(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        folder: str = "media",
    ) -> str:
        if not self.cloud_name or not self.api_key or not self.api_secret:
            logger.warning("Cloudinary credentials incomplete; falling back to local file storage")
            return await LocalStorageProvider().upload_file(content, filename, content_type, folder)

        timestamp = int(time.time())
        public_id = f"{folder}/{os.path.splitext(filename)[0]}"

        # Cloudinary signature generation: SHA-1 of sorted params + api_secret
        params_to_sign = f"folder={folder}&public_id={public_id}&timestamp={timestamp}{self.api_secret}"
        signature = hashlib.sha1(params_to_sign.encode("utf-8")).hexdigest()

        upload_url = f"https://api.cloudinary.com/v1_1/{self.cloud_name}/image/upload"
        data = {
            "api_key": self.api_key,
            "timestamp": str(timestamp),
            "folder": folder,
            "public_id": public_id,
            "signature": signature,
        }
        files = {"file": (filename, content, content_type)}

        async with httpx.AsyncClient() as client:
            resp = await client.post(upload_url, data=data, files=files, timeout=30.0)
            resp.raise_for_status()
            res_json = resp.json()
            return res_json.get("secure_url") or res_json.get("url")

    async def delete_file(self, file_url_or_path: str) -> bool:
        if not self.cloud_name or not self.api_key or not self.api_secret:
            return False

        # Extract public_id from Cloudinary URL
        try:
            parsed = urllib.parse.urlparse(file_url_or_path)
            parts = parsed.path.split("/")
            # Cloudinary URLs: /<cloud_name>/image/upload/v<version>/<folder>/<public_id>.<ext>
            if "upload" in parts:
                idx = parts.index("upload") + 2  # skip version
                public_id = "/".join(parts[idx:])
                public_id = os.path.splitext(public_id)[0]
            else:
                public_id = os.path.splitext(os.path.basename(file_url_or_path))[0]

            timestamp = int(time.time())
            params_to_sign = f"public_id={public_id}&timestamp={timestamp}{self.api_secret}"
            signature = hashlib.sha1(params_to_sign.encode("utf-8")).hexdigest()

            destroy_url = f"https://api.cloudinary.com/v1_1/{self.cloud_name}/image/destroy"
            data = {
                "public_id": public_id,
                "api_key": self.api_key,
                "timestamp": str(timestamp),
                "signature": signature,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(destroy_url, data=data, timeout=15.0)
                return resp.status_code == 200
        except Exception as e:  # noqa: BLE001
            logger.warning("Cloudinary delete failed: %s", e)
            return False


def get_storage_provider() -> BaseStorageProvider:
    """Factory function returning the configured StorageProvider."""
    backend = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    if backend == "s3":
        return S3StorageProvider()
    if backend == "cloudinary":
        return CloudinaryStorageProvider()
    return LocalStorageProvider()
