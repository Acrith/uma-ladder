from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField
from wtforms.validators import AnyOf, DataRequired, Length, NumberRange


class CreateDraftForm(FlaskForm):
    umas_per_player = IntegerField(
        "Umas per player", validators=[DataRequired(), NumberRange(min=2, max=3)]
    )
    preset_pool = SelectField(
        "Preset pool",
        choices=[("custom", "Custom"), ("g1", "G1"), ("g1+custom", "G1 + Custom")],
        validators=[DataRequired(), AnyOf(["custom", "g1", "g1+custom"])],
    )


class JoinDraftForm(FlaskForm):
    join_code = StringField(
        "Join code", validators=[DataRequired(), Length(min=4, max=16)]
    )


class RoomCodeForm(FlaskForm):
    room_code = StringField(
        "Room code", validators=[DataRequired(), Length(min=1, max=32)]
    )


class CsrfOnlyForm(FlaskForm):
    """Empty form just for CSRF-protecting POST endpoints with custom payloads."""
