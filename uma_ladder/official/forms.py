from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    DateTimeLocalField,
    IntegerField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import AnyOf, DataRequired, Length, NumberRange, Optional


class CreateOfficialRaceForm(FlaskForm):
    season_id = IntegerField("Season", validators=[DataRequired()])
    name = StringField("Race name", validators=[DataRequired(), Length(max=128)])
    # Track is required at creation time so the race has a definite course
    # before registration opens. The dropdown is populated server-side; the
    # pool filter + Random button are purely client-side helpers.
    preset_id = IntegerField("Track", validators=[DataRequired()])
    max_players = IntegerField(
        "Max players", validators=[Optional(), NumberRange(min=1, max=64)]
    )
    # HTML5 datetime-local field; the browser submits naive local time,
    # which the route interprets as UTC for storage. Optional — races
    # without a scheduled time still work; they sort by created_at.
    scheduled_at = DateTimeLocalField(
        "Scheduled at",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])
    # Race-day conditions (PR-G3). Optional so an organizer who hasn't
    # decided yet can publish a race; the service-layer validator
    # enforces the Snowy/Winter pairing when both are set.
    race_season = SelectField(
        "Season",
        choices=[("", "—"), ("Spring", "Spring"), ("Summer", "Summer"),
                 ("Autumn", "Autumn"), ("Winter", "Winter")],
        validators=[Optional(), AnyOf(["", "Spring", "Summer", "Autumn", "Winter"])],
    )
    weather = SelectField(
        "Weather",
        choices=[("", "—"), ("Sunny", "Sunny"), ("Cloudy", "Cloudy"),
                 ("Rainy", "Rainy"), ("Snowy", "Snowy")],
        validators=[Optional(), AnyOf(["", "Sunny", "Cloudy", "Rainy", "Snowy"])],
    )
    ground_condition = SelectField(
        "Ground",
        choices=[("", "—"), ("Firm", "Firm"), ("Good", "Good"),
                 ("Soft", "Soft"), ("Heavy", "Heavy")],
        validators=[Optional(), AnyOf(["", "Firm", "Good", "Soft", "Heavy"])],
    )
    # PR-J13 — race targeting. Public is the default. Private
    # adds an invitee allowlist that gates both view + register.
    # Club (PR-L1) limits visibility to the organizer's uma.moe
    # club; the route filters this choice out of the form when
    # the organizer has no club_id synced yet.
    visibility = SelectField(
        "Visibility",
        choices=[
            ("public", "Public"),
            ("private", "Private"),
            ("club", "Club only"),
        ],
        default="public",
        validators=[DataRequired(), AnyOf(["public", "private", "club"])],
    )


class RoomCodeForm(FlaskForm):
    room_code = StringField(
        "Room code", validators=[DataRequired(), Length(min=1, max=32)]
    )


class ResultsForm(FlaskForm):
    """Bare form just for CSRF; result rows are read directly from request.form."""


class CsrfOnlyForm(FlaskForm):
    """Empty form used to gate state-changing POSTs (cancel, remove)."""


class ResultsScreenshotForm(FlaskForm):
    image = FileField(
        "Result screenshot",
        validators=[
            FileRequired(),
            FileAllowed(
                ("png", "jpg", "jpeg", "webp"), "Use a PNG / JPG / WEBP image."
            ),
        ],
    )
