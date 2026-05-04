from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import IntegerField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class CreateOfficialRaceForm(FlaskForm):
    season_id = IntegerField("Season", validators=[DataRequired()])
    name = StringField("Race name", validators=[DataRequired(), Length(max=128)])
    preset_id = IntegerField("Preset", validators=[Optional()])
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
