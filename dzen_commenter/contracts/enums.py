from enum import Enum


class CommentStatus(str, Enum):
    NEW = "new"
    ANSWERED = "answered"
    SKIPPED = "skipped"
    ERROR = "error"


class ReplyStatus(str, Enum):
    GENERATED = "generated"
    PUBLISHED = "published"
    ERROR = "error"
    SKIPPED = "skipped"
