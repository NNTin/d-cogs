from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement_or_raise

from .langcore import langcore
from .models import BaseModel, Conversation, GuildConfig

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)

__all__ = ["langcore", "BaseModel", "Conversation", "GuildConfig"]


async def setup(bot: Red) -> None:
    await bot.add_cog(langcore(bot))
