"""
Backward-compatible wrapper that re-exports shared models from cogchain.
"""

from cogchain.models import (
    BaseModel,
    Conversation,
    GuildConfig,
    ProviderSelectionConfig,
)

__all__ = ["BaseModel", "Conversation", "GuildConfig", "ProviderSelectionConfig"]
