"""
tests/test_notifications.py — Mixtape

Tests for notification behavior when songs are rated.
"""

import pytest
from app import create_app, db
from models import Notification, Song, User
from services.notification_service import rate_song


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seeded_song(app):
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Shared Track", artist="The Collective", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rate_song_notifies_the_original_sharer(app, seeded_song):
    with app.app_context():
        rating = rate_song(seeded_song["rater"].id, seeded_song["song"].id, 5)

        notifications = db.session.query(Notification).filter_by(
            user_id=seeded_song["sharer"].id
        ).all()

        assert rating.score == 5
        assert len(notifications) == 1
        assert notifications[0].notification_type == "song_rated"


def test_rate_song_does_not_notify_self(app):
    with app.app_context():
        user = User(username="solo", email="solo@example.com")
        db.session.add(user)
        db.session.flush()

        song = Song(title="My Song", artist="Solo Artist", shared_by=user.id)
        db.session.add(song)
        db.session.commit()

        rate_song(user.id, song.id, 4)

        notifications = db.session.query(Notification).filter_by(user_id=user.id).all()
        assert notifications == []