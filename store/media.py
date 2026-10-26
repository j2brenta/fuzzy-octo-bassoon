"""Immutable bounded media storage shared by Telegram image capture."""

import io
import os
from pathlib import Path
import tempfile


class MediaError(RuntimeError):
    pass


class LimitedBuffer(io.BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, data):
        if self.tell() + len(data) > self.limit:
            raise MediaError("Download exceeded the media size limit.")
        return super().write(data)


def publish_original(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise MediaError("Saved media differs; original preserved.") from None
    finally:
        temporary.unlink(missing_ok=True)
