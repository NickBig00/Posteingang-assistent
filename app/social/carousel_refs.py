"""Which images carousel pages use (kept apart to avoid an import cycle with `images`)."""

from sqlmodel import Session, select

from app.social.models import CarouselPage


def asset_in_carousel(session: Session, asset_id: int) -> bool:
    statement = select(CarouselPage.id).where(CarouselPage.asset_id == asset_id)
    return session.exec(statement).first() is not None
