from .rag import RAGUtils

__red_end_user_data_statement__ = "This cog stores per-guild RAG configuration settings."


async def setup(bot):
    cog = RAGUtils(bot)
    await bot.add_cog(cog)
