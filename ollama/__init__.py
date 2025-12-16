from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement_or_raise

from .ollama import ollama
from .models import OllamaConfig, OllamaGuildConfig

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)

__all__ = ["ollama", "OllamaGuildConfig", "OllamaConfig"]


async def setup(bot: Red) -> None:
    await bot.add_cog(ollama(bot))
