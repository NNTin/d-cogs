import asyncio
from typing import Literal

from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from .mixin import ConfigMixin, ListenerMixin, WebsocketServerMixin

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class dworld(ConfigMixin, WebsocketServerMixin, ListenerMixin, commands.Cog):
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

        # Initialize the mixin
        ConfigMixin.__init__(self)
        WebsocketServerMixin.__init__(self)
        ListenerMixin.__init__(self)

    async def cog_load(self) -> None:
        await super().cog_load()

        print("Starting websockets server...")
        asyncio.create_task(self.server.start())

    async def cog_unload(self) -> None:
        await super().cog_unload()
        print("Stopping websockets server...")
        await self.server.stop()

    async def red_delete_data_for_user(
        self, *, requester: RequestType, user_id: int
    ) -> None:
        super().red_delete_data_for_user(requester=requester, user_id=user_id)
