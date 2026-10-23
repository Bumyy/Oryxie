import discord


DM_LOG_CHANNEL_ID = 1562428318043602965


async def post_dm_report(bot, content, logger, context):
    try:
        channel = bot.get_channel(DM_LOG_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(DM_LOG_CHANNEL_ID)
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except Exception:
        logger.exception(
            "Could not post %s DM report to required log channel %s.",
            context,
            DM_LOG_CHANNEL_ID,
        )
        return False
