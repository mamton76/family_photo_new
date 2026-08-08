"""Storage provider abstractions and skeleton implementations."""

from photoarchive.storage.base import (
    ReadableStorage,
    StorageError,
    WritableStorage,
)

__all__ = ["ReadableStorage", "StorageError", "WritableStorage"]
