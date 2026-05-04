from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import IntegerField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


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
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])


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
