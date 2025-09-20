import asyncio
from typing import Literal

from d_back.server import WebSocketServer
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class dworld(commands.Cog):
    """
    d-world implements d-back
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=257263088,
            force_registration=True,
        )
        self.server = WebSocketServer(port=3000, host="localhost")

    @commands.command()
    async def startdzone(self, ctx):
        """starts d-back server"""
        await ctx.send("starting d-back server...")

        asyncio.create_task(self.server.start())

    @commands.command()
    async def stopdzone(self, ctx):
        """stops d-back server"""
        await ctx.send("stopping d-back server...")
        await self.server.stop()

    async def cog_load(self) -> None:
        await super().cog_load()
        print("Starting websockets server...")
        # TODO: should instantiate an object and from there call start_server
        # asyncio.create_task(d_back.start_server())

    async def cog_unload(self) -> None:
        await super().cog_unload()
        print("Stopping websockets server...")
        await self.server.stop()

    async def red_delete_data_for_user(
        self, *, requester: RequestType, user_id: int
    ) -> None:
        super().red_delete_data_for_user(requester=requester, user_id=user_id)
