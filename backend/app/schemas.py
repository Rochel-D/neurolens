import re

from pydantic import BaseModel, Field, field_validator

EMAIL = r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$"


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=160, pattern=EMAIL)
    password: str = Field(min_length=8, max_length=72)  # password is never sanitised — only validated

    @field_validator("password")
    @classmethod
    def strong(cls, v: str) -> str:
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("Password needs letters and digits")
        return v


class LoginIn(BaseModel):
    email: str = Field(max_length=160, pattern=EMAIL)
    password: str = Field(min_length=1, max_length=72)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    thread_id: str | None = Field(default=None, max_length=40, pattern=r"^[a-zA-Z0-9_-]*$")
    audience: str = Field(default="patient", pattern="^(patient|doctor)$")
    report_id: int | None = None
    mri_id: int | None = None
    language: str = Field(default="en", pattern="^(en|hi|kok)$")


class ConsentIn(BaseModel):
    consent: bool


class ProfileIn(BaseModel):
    sex: str | None = Field(default=None, pattern="^(male|female|)$")
    age_group: str | None = Field(default=None, max_length=20)
    preferred_view: str | None = Field(default=None, pattern="^(patient|doctor|)$")
    notes: str | None = Field(default=None, max_length=200)
    preferred_language: str | None = Field(default=None, pattern="^(en|hi|kok|)$")


class FeedbackIn(BaseModel):
    trace_id: str = Field(min_length=6, max_length=40, pattern=r"^[a-f0-9]+$")
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=500)
