"""Database tables.

Privacy design:
* Uploaded files are never written to disk. Only parsed lab values / MRI predictions are kept.
* Long-term memory (MemoryItem) is written only when the user has given consent.
* "Delete my data" removes reports, analyses, chat, memory and profile in one transaction.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UserProfile(Base):
    """Semantic long-term memory. Saved only with consent; can be deleted."""

    __tablename__ = "user_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    memory_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # sex, age_group, preferred_view, notes
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MemoryItem(Base):
    """Episodic long-term memory: short summaries of past reports, scans and stated facts."""

    __tablename__ = "memory_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # report | mri | fact
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatMessage(Base):
    """Short-term memory source: the agent sees the last 10 messages of the current thread."""

    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LabReport(Base):
    __tablename__ = "lab_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    results: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    report_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD read from the report, else upload date
    source: Mapped[str] = mapped_column(String(30), default="text")  # pdf | text | photo:gemini | photo:tesseract
    status: Mapped[str] = mapped_column(String(20), default="final")  # final | needs_review (photo reports)
    explanations: Mapped[dict] = mapped_column(JSON, default=dict)  # cache for PDF export: {"patient:en": "..."}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MriScan(Base):
    __tablename__ = "mri_scans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(40), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    probabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    heatmap_png: Mapped[str] = mapped_column(Text, default="")  # base64 overlay
    image_png: Mapped[str] = mapped_column(Text, default="")  # base64 224×224 copy of the slice (for the PDF)
    explanations: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Trace(Base):
    """One row per agent run: which tools ran, which provider/key answered, verifier outcome."""

    __tablename__ = "traces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    thread_id: Mapped[str] = mapped_column(String(40))
    intent: Mapped[str] = mapped_column(String(30), default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    providers: Mapped[list] = mapped_column(JSON, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    guard_flags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | blocked | error
    language: Mapped[str] = mapped_column(String(10), default="en")
    message: Mapped[str] = mapped_column(Text, default="")  # redacted user message, for admin search
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Feedback(Base):
    """Thumbs up / down on an answer, shown on the admin page."""

    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trace_id: Mapped[str] = mapped_column(String(40), index=True)
    rating: Mapped[int] = mapped_column(Integer)  # +1 / -1
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Export(Base):
    """Generated PDFs. Owner-only download, expires after EXPORT_TTL_HOURS."""

    __tablename__ = "exports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(160))
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Setting(Base):
    """Admin overrides that survive restarts (model names, disabled key slots)."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class KBOverride(Base):
    """Knowledge-base edits from the admin editor, layered over the JSON seed files."""

    __tablename__ = "kb_overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), index=True)  # lab | note
    item_id: Mapped[str] = mapped_column(String(60), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
