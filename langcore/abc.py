"""
Backward-compatible wrapper that re-exports interfaces from the cogchain package.

The concrete implementations live in individual cogs. langcore keeps these imports so
existing code paths (`from langcore.abc import ...`) continue to work after the move.
"""

from cogchain.interfaces import (
    ChainProvider,
    ChainStore,
    ExtensionContext,
    MessageHandler,
    SubAgent,
)

__all__ = [
    "ChainProvider",
    "ChainStore",
    "ExtensionContext",
    "MessageHandler",
    "SubAgent",
]
