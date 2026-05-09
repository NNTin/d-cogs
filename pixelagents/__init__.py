try:
    from redbot.core.bot import Red
    from redbot.core.utils import get_end_user_data_statement_or_raise
except Exception:
    Red = None  # type: ignore[assignment,misc]

    def get_end_user_data_statement_or_raise(_: str) -> str:  # type: ignore[override]
        return ""

try:
    from .pixelagents import pixelagents
    __all__ = ["pixelagents"]
except Exception:
    pixelagents = None  # type: ignore[assignment]
    __all__ = []

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot: "Red") -> None:  # type: ignore[name-defined]
    if pixelagents is None:
        raise RuntimeError("pixelagents failed to import — check dependencies")
    await bot.add_cog(pixelagents(bot))
