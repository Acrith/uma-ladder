from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired


class UploadForm(FlaskForm):
    image = FileField(
        "Screenshot",
        validators=[
            FileRequired(),
            FileAllowed(
                ("png", "jpg", "jpeg", "webp"), "Use a PNG / JPG / WEBP image."
            ),
        ],
    )


class CsrfOnlyForm(FlaskForm):
    pass
