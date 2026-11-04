"""Video-to-image sampling extension for Inline Studio."""

from inline_core.extensions.api import ExtensionRegistrar

from .sampler import VideoToImages


def register(reg: ExtensionRegistrar) -> None:
    reg.nodes(VideoToImages)
