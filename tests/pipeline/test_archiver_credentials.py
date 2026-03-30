"""
Tests for R2 credential wiring in archiver.py.

Verifies that get_r2_client() reads credentials from Keychain (via get_secret)
rather than directly from environment variables.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import archiver

MOCK_SECRETS = {
    "R2_ACCESS_KEY_ID": "test-access-key",
    "R2_SECRET_ACCESS_KEY": "test-secret-key",
    "R2_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
}


def test_get_r2_client_reads_from_keychain():
    """R2 client should read credentials from Keychain secrets."""
    mock_boto3 = MagicMock()

    with patch.object(archiver, "get_secret", side_effect=lambda k, **kw: MOCK_SECRETS.get(k)):
        with patch.object(archiver, "boto3", mock_boto3):
            archiver.get_r2_client()

            mock_boto3.client.assert_called_once()
            call_kwargs = mock_boto3.client.call_args
            assert call_kwargs[1]["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
            assert call_kwargs[1]["aws_access_key_id"] == "test-access-key"
            assert call_kwargs[1]["aws_secret_access_key"] == "test-secret-key"


def test_get_r2_client_uses_s3_service():
    """boto3.client should be called with 's3' as the service name."""
    mock_boto3 = MagicMock()

    with patch.object(archiver, "get_secret", side_effect=lambda k, **kw: MOCK_SECRETS.get(k)):
        with patch.object(archiver, "boto3", mock_boto3):
            archiver.get_r2_client()

            args = mock_boto3.client.call_args[0]
            assert args[0] == "s3"


def test_upload_to_r2_returns_true_on_success():
    """upload_to_r2 should return True when boto3 upload_file succeeds."""
    mock_client = MagicMock()
    mock_client.upload_file.return_value = None  # upload_file returns None on success

    with patch.object(archiver, "get_secret", side_effect=lambda k, **kw: MOCK_SECRETS.get(k)):
        with patch.object(archiver, "get_r2_client", return_value=mock_client):
            result = archiver.upload_to_r2("/tmp/some-archive.tar.gz", "2025/06/some-archive.tar.gz")
            assert result is True
            mock_client.upload_file.assert_called_once()


def test_upload_to_r2_returns_false_on_exception():
    """upload_to_r2 should return False when boto3 raises an exception."""
    mock_client = MagicMock()
    mock_client.upload_file.side_effect = RuntimeError("connection refused")

    with patch.object(archiver, "get_secret", side_effect=lambda k, **kw: MOCK_SECRETS.get(k)):
        with patch.object(archiver, "get_r2_client", return_value=mock_client):
            result = archiver.upload_to_r2("/tmp/some-archive.tar.gz", "2025/06/some-archive.tar.gz")
            assert result is False
