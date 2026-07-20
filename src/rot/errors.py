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


class ParserError(RotError):
    """An AI parser could not convert a script."""


class DownloadError(RotError):
    """A remote media asset could not be downloaded."""


class ClipAnalysisError(RotError):
    """A media asset could not be analyzed or split into clips."""
