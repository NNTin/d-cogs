# SPDX-FileCopyrightText: 2025 d-cogs contributors
# SPDX-License-Identifier: MPL-2.0

from .modreload import ModReload


async def setup(bot) -> None:
    await bot.add_cog(ModReload(bot))
