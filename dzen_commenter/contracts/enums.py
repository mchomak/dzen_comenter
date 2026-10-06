from enum import Enum


class CommentStatus(str, Enum):
    NEW = "new"
    GENERATING = "generating"
    GENERATED = "generated"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    SKIPPED = "skipped"
    GENERATION_RETRY = "generation_retry"
    GENERATION_ERROR = "generation_error"
    PUBLICATION_RETRY = "publication_retry"
    PUBLICATION_ERROR = "publication_error"
    PUBLICATION_UNCONFIRMED = "publication_unconfirmed"


class ReplyStatus(str, Enum):
    GENERATED = "generated"
    PUBLISHED = "published"
    ERROR = "error"
    SKIPPED = "skipped"
    UNCONFIRMED = "unconfirmed"


class PublicationFailureOutcome(str, Enum):
    RETRY = "retry"
    TERMINAL = "terminal"


class GenerationFailureOutcome(str, Enum):
    RETRY = "retry"
    TERMINAL = "terminal"
