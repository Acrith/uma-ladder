from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import IntegerField, StringField, TextAreaField
from wtforms.validators import Length, Optional


class ProfileForm(FlaskForm):
    display_name = StringField("Display name", validators=[Optional(), Length(max=64)])
    avatar_url = StringField("Avatar URL", validators=[Optional(), Length(max=512)])
    avatar_image = FileField(
        "Avatar upload",
        validators=[
            Optional(),
            FileAllowed(
                ("png", "jpg", "jpeg", "webp"), "Use a PNG / JPG / WEBP image."
            ),
        ],
    )
    description = TextAreaField("About you", validators=[Optional(), Length(max=2000)])
    friend_code = StringField("Friend code", validators=[Optional(), Length(max=32)])
    discord_handle = StringField("Discord handle", validators=[Optional(), Length(max=64)])
    oshi_character_id = IntegerField("Oshi", validators=[Optional()])
