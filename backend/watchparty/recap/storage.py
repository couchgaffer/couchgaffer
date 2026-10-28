"""Recaps in Azure Storage, keyless (#15): the bundle and videos in the `recaps` container, one queue message
per session for the job, and read links signed with an Entra user delegation key (no account keys exist).

Blob storage serves the videos itself, with the byte ranges phones and Safari need to play them.
"""

import asyncio
import datetime as dt
import gzip
import json
import re
from typing import Any

from azure.storage.blob import BlobSasPermissions, UserDelegationKey, generate_blob_sas

CONTAINER = QUEUE = "recaps"
LINK_DAYS = 7  # the longest a user delegation key may live; recaps are watched at full time
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")


def input_blob(session_id: str) -> str:
    if not SESSION_ID.fullmatch(session_id):
        raise ValueError("not a session id")
    return f"{session_id}/input.json.gz"


def video_blob(session_id: str, manager: str) -> str:
    return f"{session_id}/{manager}.mp4"


def done_blob(session_id: str) -> str:
    return f"{session_id}/done.json"


class AzureRecaps:
    """The app's side: submit a session, then hand out links once the job is done."""

    def __init__(self, blobs: Any, queue: Any) -> None:
        self.blobs, self.queue = blobs, queue  # BlobServiceClient, QueueClient (sync; called off the loop)
        self.container = blobs.get_container_client(CONTAINER)
        self._key: UserDelegationKey | None = None

    @classmethod
    def for_account(cls, account: str) -> "AzureRecaps":
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        from azure.storage.queue import QueueClient

        credential = DefaultAzureCredential()
        blobs = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential)
        queue = QueueClient(f"https://{account}.queue.core.windows.net", QUEUE, credential=credential)
        return cls(blobs, queue)

    async def submit(self, session_id: str, bundle: dict[str, Any]) -> None:
        def pack() -> bytes:  # a whole match: hundreds of milliseconds, kept off the event loop (#49)
            return gzip.compress(json.dumps(bundle).encode())

        data = await asyncio.to_thread(pack)
        await asyncio.to_thread(self.container.upload_blob, input_blob(session_id), data, overwrite=True)
        await asyncio.to_thread(self.queue.send_message, json.dumps({"session_id": session_id}))

    def _delegation_key(self, now: dt.datetime) -> tuple[UserDelegationKey, dt.datetime]:
        """The key and when it expires; refreshed daily, so every link lasts at least six days."""
        expiry = dt.datetime.fromisoformat(self._key.signed_expiry) if self._key and self._key.signed_expiry else now
        if self._key is None or expiry - now < dt.timedelta(days=LINK_DAYS - 1):
            expiry = now + dt.timedelta(days=LINK_DAYS)
            self._key = self.blobs.get_user_delegation_key(now - dt.timedelta(minutes=5), expiry)
        return self._key, expiry

    def _links(self, session_id: str, managers: list[str]) -> dict[str, str] | None:
        if not self.container.get_blob_client(done_blob(session_id)).exists():
            return None
        now = dt.datetime.now(dt.UTC)
        key, expiry = self._delegation_key(now)
        links = {}
        for manager in managers:
            name = video_blob(session_id, manager)
            sas = generate_blob_sas(self.blobs.account_name, CONTAINER, name, user_delegation_key=key,
                                    permission=BlobSasPermissions(read=True), start=now - dt.timedelta(minutes=5),
                                    expiry=expiry, protocol="https")  # fmt: skip
            links[manager] = f"{self.container.get_blob_client(name).url}?{sas}"
        return links

    async def links(self, session_id: str, managers: list[str]) -> dict[str, str] | None:
        return await asyncio.to_thread(self._links, session_id, managers)
