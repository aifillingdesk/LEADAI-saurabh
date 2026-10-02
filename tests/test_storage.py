"""
Tests for Phase 5: Pluggable Media Storage (Local, S3, Cloudinary).
"""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.storage import service as ss


@pytest.mark.asyncio
async def test_local_storage_lifecycle():
    with tempfile.TemporaryDirectory() as temp_dir:
        provider = ss.LocalStorageProvider(base_dir=temp_dir)
        content = b"fake image bytes content"
        filename = "logo_test.png"
        folder = "branding"

        # 1. Upload
        url = await provider.upload_file(content, filename, "image/png", folder=folder)
        assert url == f"/static/{folder}/{filename}"

        # Verify on filesystem
        expected_path = os.path.join(temp_dir, folder, filename)
        assert os.path.isfile(expected_path)
        with open(expected_path, "rb") as f:  # noqa: ASYNC230
            assert f.read() == content

        # 2. Delete
        deleted = await provider.delete_file(url)
        assert deleted is True
        assert not os.path.isfile(expected_path)

        # 3. Delete non-existent
        deleted_again = await provider.delete_file(url)
        assert deleted_again is False


@pytest.mark.asyncio
async def test_s3_storage_provider_upload_and_delete():
    provider = ss.S3StorageProvider(
        bucket="my-test-bucket",
        region="us-west-2",
        access_key="fake_key",
        secret_key="fake_secret",
        custom_domain="https://cdn.example.com",
    )

    content = b"test payload"
    filename = "doc.png"

    # Test upload with custom domain
    mock_boto = MagicMock()
    mock_s3 = MagicMock()
    mock_boto.client.return_value = mock_s3

    with patch.dict("sys.modules", {"boto3": mock_boto}):
        url = await provider.upload_file(content, filename, "image/png", folder="media")
        assert "cdn.example.com" in url
        assert "media/doc.png" in url
        mock_s3.put_object.assert_called_once_with(
            Bucket="my-test-bucket",
            Key="media/doc.png",
            Body=content,
            ContentType="image/png",
        )

        deleted = await provider.delete_file(url)
        assert deleted is True
        mock_s3.delete_object.assert_called_once_with(
            Bucket="my-test-bucket",
            Key="media/doc.png",
        )


@pytest.mark.asyncio
async def test_cloudinary_storage_provider():
    provider = ss.CloudinaryStorageProvider(
        cloud_name="test_cloud",
        api_key="123456",
        api_secret="secret_abc",
    )

    content = b"cloudinary image content"
    filename = "banner.jpg"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"secure_url": "https://res.cloudinary.com/test_cloud/image/upload/v1/banners/banner.jpg"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        url = await provider.upload_file(content, filename, "image/jpeg", folder="banners")
        assert "cloudinary.com" in url
        assert mock_post.called

        # Test delete
        del_resp = MagicMock()
        del_resp.status_code = 200
        mock_post.return_value = del_resp

        deleted = await provider.delete_file(url)
        assert deleted is True


def test_storage_provider_factory():
    with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
        p1 = ss.get_storage_provider()
        assert isinstance(p1, ss.LocalStorageProvider)

    with patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}):
        p2 = ss.get_storage_provider()
        assert isinstance(p2, ss.S3StorageProvider)

    with patch.dict(os.environ, {"STORAGE_BACKEND": "cloudinary"}):
        p3 = ss.get_storage_provider()
        assert isinstance(p3, ss.CloudinaryStorageProvider)

    # Defaults to local when empty or unknown
    with patch.dict(os.environ, {"STORAGE_BACKEND": "unknown_provider"}):
        p4 = ss.get_storage_provider()
        assert isinstance(p4, ss.LocalStorageProvider)
