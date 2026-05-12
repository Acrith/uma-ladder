from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired, EqualTo, Length


class RegisterForm(FlaskForm):
    # PR-Q4 — invite_code is optional at the form level; the route
    # enforces "required if invite-only flag is on" so the field can
    # stay dormant on the page when the gate is off.
    invite_code = StringField("Invite code", validators=[Length(max=32)])
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])


class RequestResetForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
