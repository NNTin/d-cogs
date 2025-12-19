from .spoilarr import spoilarr


async def setup(bot):
    await bot.add_cog(spoilarr(bot))
