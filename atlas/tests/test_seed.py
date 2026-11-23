"""The seed CI uses to test migrations on data (lesson 7.2) must keep matching the models."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas.models import Citation, Conversation, Feedback, Message
from tests.seed import seed


def test_the_seed_fills_the_tables_migrations_are_tested_on(session: Session) -> None:
    # Fails when a model gains a required column the seed doesn't set: the reminder to
    # update the seed along with the schema.
    seed(session)
    session.flush()

    for model in (Conversation, Message, Citation, Feedback):
        rows = session.scalar(select(func.count()).select_from(model)) or 0
        assert rows >= 1, model.__tablename__
