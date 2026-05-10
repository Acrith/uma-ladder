from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import Length, Optional, Regexp

# PR-P1 — keep in sync with services.profiles.AVATAR_BORDER_PALETTE.
# Tuples are (form-value, display-label). Empty value = default
# border (no override). Order is the rendered-on-page order.
_BORDER_CHOICES: list[tuple[str, str]] = [
    ("", "Default"),
    ("cyan", "Cyan"),
    ("fuchsia", "Fuchsia"),
    ("emerald", "Emerald"),
    ("amber", "Amber"),
    ("rose", "Rose"),
    ("violet", "Violet"),
    ("sky", "Sky"),
    ("indigo", "Indigo"),
    ("lime", "Lime"),
    ("orange", "Orange"),
    ("pink", "Pink"),
    ("slate", "Slate"),
]


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
    # Discord user ID = a 17-20 digit decimal snowflake. Stored as string
    # so JSON / DB serialisation never loses precision on large IDs.
    discord_user_id = StringField(
        "Discord user ID",
        validators=[
            Optional(),
            Regexp(
                r"^\d{15,20}$",
                message="Use the numeric ID (right-click your Discord name → Copy User ID).",
            ),
        ],
    )
    oshi_character_id = IntegerField("Oshi", validators=[Optional()])
    avatar_border = SelectField(
        "Avatar border",
        choices=_BORDER_CHOICES,
        validators=[Optional()],
    )
