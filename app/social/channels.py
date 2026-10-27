"""Platform limits per channel. Platform facts, not brand configuration."""

from dataclasses import dataclass

from app.social.models import Channel


@dataclass(frozen=True)
class ChannelSpec:
    max_length: int
    # Hard platform limit for hashtags; None if the platform sets none.
    max_hashtags: int | None


CHANNEL_SPECS: dict[Channel, ChannelSpec] = {
    Channel.INSTAGRAM_POST: ChannelSpec(max_length=2200, max_hashtags=30),
    # Story text is an overlay on the image: kept deliberately short.
    Channel.INSTAGRAM_STORY: ChannelSpec(max_length=250, max_hashtags=10),
    Channel.FACEBOOK: ChannelSpec(max_length=63206, max_hashtags=None),
    Channel.LINKEDIN: ChannelSpec(max_length=3000, max_hashtags=None),
}
