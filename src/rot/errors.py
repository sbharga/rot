"""Exceptions raised by rot."""


class RotError(Exception):
    """Base exception for all expected rot failures."""


class ConfigurationError(RotError):
    """A project or render setting is invalid."""


class ScriptError(RotError):
    """A script cannot be parsed or validated."""


class DependencyError(RotError):
    """A required executable or optional Python integration is unavailable."""


class ProbeError(RotError):
    """FFprobe could not inspect an asset."""


class RenderError(RotError):
    """FFmpeg could not render a project."""


class VoiceError(RotError):
    """A voice provider could not synthesize speech."""


class AlignmentError(RotError):
    """A word aligner could not align speech."""


class TranscriptionError(RotError):
    """A speech-to-text provider could not transcribe clip audio."""


class ParserError(RotError):
    """An AI parser could not convert a script."""


class DownloadError(RotError):
    """A remote media asset could not be downloaded."""


class ClipAnalysisError(RotError):
    """A media asset could not be analyzed or split into clips."""


class PublishError(RotError):
    """A platform rejected or could not complete a publishing operation."""


class PublishTimeoutError(PublishError):
    """A remote publishing operation did not reach a terminal state in time.

    Args:
        message: Human-readable timeout description.
        platform: Platform that timed out.
        remote_id: Existing upload or container identifier.
    """

    def __init__(self, message: str, *, platform: str, remote_id: str) -> None:
        """Record a resumable remote identifier with the timeout.

        Args:
            message: Human-readable timeout description.
            platform: Platform that timed out.
            remote_id: Existing upload or container identifier.
        """

        super().__init__(message)
        self.platform = platform
        self.remote_id = remote_id
