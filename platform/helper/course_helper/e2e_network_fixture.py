"""Socket-free Wikimedia-compatible fixture used only by the browser E2E server."""

from __future__ import annotations

import base64
from typing import Any

from course_helper.network_visuals import ProviderDownload, WikimediaApiClient


_IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAFElEQVR4nGNUSX7NAAZMEIqBgQEAGXkBdoqoE6wAAAAASUVORK5CYII="
)
_PAGE: dict[str, Any] = {
    "batchcomplete": True,
    "query": {
        "pages": [
            {
                "pageid": 42,
                "ns": 6,
                "title": "File:Verified.png",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Verified.png",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Verified.png",
                        "mime": "image/png",
                        "size": len(_IMAGE),
                        "width": 3,
                        "height": 2,
                        "sha1": "b57f038cb32a96016a5a6913c8048767b700312a",
                        "extmetadata": {
                            "ObjectName": {"value": "Verified diagram"},
                            "ImageDescription": {"value": "A socket-free E2E provider fixture."},
                            "Artist": {"value": "Example Creator"},
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-sa/4.0/"
                            },
                        },
                    }
                ],
            }
        ]
    },
}


class _FixtureTransport:
    def get_json(self, _url: str, *, max_bytes: int) -> dict[str, Any]:
        if max_bytes != 1024 * 1024:
            raise ValueError("E2E fixture JSON ceiling changed")
        return _PAGE

    def get_bytes(self, _url: str, *, max_bytes: int) -> ProviderDownload:
        if max_bytes != 32 * 1024 * 1024:
            raise ValueError("E2E fixture media ceiling changed")
        return ProviderDownload(
            body=_IMAGE,
            final_url="https://upload.wikimedia.org/wikipedia/commons/a/ab/Verified.png",
            media_type="image/png",
            content_length=len(_IMAGE),
            redirect_count=0,
            resolved_host_count=1,
        )


def fixture_network_provider() -> WikimediaApiClient:
    return WikimediaApiClient(_FixtureTransport())


__all__ = ["fixture_network_provider"]
