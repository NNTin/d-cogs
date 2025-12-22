class CogchainError(Exception):
    """Base exception for shared cogchain contracts."""


class ProviderNotRegistered(CogchainError):
    """Raised when a requested provider is missing."""


class StoreUnavailable(CogchainError):
    """Raised when vector storage is unavailable."""


class ProviderSelectionError(CogchainError):
    """Raised when provider selection fails."""


class NoAvailableProviders(ProviderSelectionError):
    """Raised when no providers are available after applying strategy."""
