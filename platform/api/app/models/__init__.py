"""Importing this package registers every table on `Base.metadata` (needed by Alembic and tests)."""

from app.models.devices import Device, DeviceConnection, DeviceCredential, DeviceEvent, DevicePairing
from app.models.people import ConsentRecord, Person, PersonSignature, VoiceProfile
from app.models.recordings import (
    ProcessingJob,
    Recording,
    RecordingAnalysis,
    Transcript,
    TranscriptSegment,
)
from app.models.system import AuditEvent, ContactMessage, LiveEvent, Worker
from app.models.tenancy import AuthSession, Company, EmailToken, Invitation, Membership, User

__all__ = [
    "AuditEvent",
    "AuthSession",
    "Company",
    "ConsentRecord",
    "ContactMessage",
    "Device",
    "DeviceConnection",
    "DeviceCredential",
    "DeviceEvent",
    "DevicePairing",
    "EmailToken",
    "Invitation",
    "LiveEvent",
    "Membership",
    "Person",
    "PersonSignature",
    "ProcessingJob",
    "Recording",
    "RecordingAnalysis",
    "Transcript",
    "TranscriptSegment",
    "User",
    "VoiceProfile",
    "Worker",
]
