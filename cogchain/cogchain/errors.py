class CogchainError(Exception):
    """Base exception for shared cogchain contracts."""


class ProviderNotRegistered(CogchainError):
    """Raised when a requested provider is missing."""


class StoreUnavailable(CogchainError):
    """Raised when vector storage is unavailable."""
