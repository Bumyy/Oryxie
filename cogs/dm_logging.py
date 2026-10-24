import logging
import os

import discord


log = logging.getLogger(__name__)
_configured_channel_id = os.getenv("STOPPED_FLIGHT_DM_LOG_CHANNEL_ID", "").strip()
if _configured_channel_id and (
    not _configured_channel_id.isdigit() or int(_configured_channel_id) <= 0
):
    log.error(
        "STOPPED_FLIGHT_DM_LOG_CHANNEL_ID must be a positive numeric channel ID."
    )
    _configured_channel_id = ""

DM_LOG_CHANNEL_ID = int(_configured_channel_id or "1562428318043602965")
DISCORD_MESSAGE_LIMIT = 2000


def _split_report_content(content):
    remaining = str(content)
    chunks = []
    while len(remaining) > DISCORD_MESSAGE_LIMIT:
        split_at = remaining.rfind("\n", 0, DISCORD_MESSAGE_LIMIT + 1)
        if split_at <= 0:
            split_at = DISCORD_MESSAGE_LIMIT
        else:
            split_at += 1
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


async def post_dm_report(bot, content, logger, context):
    try:
        channel = bot.get_channel(DM_LOG_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(DM_LOG_CHANNEL_ID)
        for chunk in _split_report_content(content):
            await channel.send(
                chunk,
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
