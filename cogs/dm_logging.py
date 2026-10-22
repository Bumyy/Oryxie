import os

import discord


DM_LOG_CHANNEL_ID = int(
    os.getenv("STOPPED_FLIGHT_DM_LOG_CHANNEL_ID", "0") or 0
)
STAFF_CHANNEL_ID = int(
    os.getenv("STOPPED_FLIGHT_STAFF_CHANNEL_ID", "0") or 0
)


async def post_dm_report(bot, content, logger, context):
    channel_ids = list(
        dict.fromkeys(
            channel_id
            for channel_id in (DM_LOG_CHANNEL_ID, STAFF_CHANNEL_ID)
            if channel_id
        )
    )
    if not channel_ids:
        logger.error(
            "Cannot post %s DM report: neither the DM-log nor staff channel "
            "is configured.",
            context,
        )
        return False

    for channel_id in channel_ids:
        try:
            channel = bot.get_channel(channel_id)
            if channel is None:
                channel = await bot.fetch_channel(channel_id)
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if channel_id != DM_LOG_CHANNEL_ID:
                logger.warning(
                    "Posted %s DM report to staff channel %s because the "
                    "dedicated DM-log channel was unavailable.",
                    context,
                    channel_id,
                )
            return True
        except Exception:
            logger.exception(
                "Could not post %s DM report to channel %s.",
                context,
                channel_id,
            )

    logger.error(
        "DM report for %s could not be delivered to any configured log channel.",
        context,
    )
    return False
