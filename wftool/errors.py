"""Exceptions raised by the engine. Each carries a short machine code and a readable message."""


class WFError(Exception):
    code = "error"

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or []


class DefinitionError(WFError):
    """A workflow definition is invalid; `details` lists every problem found."""
    code = "definition_invalid"


class PublishError(WFError):
    """A workflow cannot be published; `details` lists every problem found."""
    code = "publish_blocked"


class ValidationError(WFError):
    """Form data does not satisfy the form; `details` lists field problems."""
    code = "form_invalid"


class StateError(WFError):
    """The action is not allowed in the current state of a request or task."""
    code = "state"


class NotFound(WFError):
    code = "not_found"


class ResolveError(WFError):
    """A step's recipient could not be resolved to a person."""
    code = "unresolved"
