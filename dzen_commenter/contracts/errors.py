class SourceCommentUnavailableError(LookupError):
    """The source comment could not be found in the bounded Dzen feed search."""


class PublicationUnconfirmedError(RuntimeError):
    """A submit may have reached Dzen, but the reply was not confirmed."""
