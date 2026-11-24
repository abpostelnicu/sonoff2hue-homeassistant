"""Failures that the .NET app doesn't expect."""


class UnexpectedError(Exception):
    """Where the .NET app's code throws an exception that it doesn't catch as a failure it expects, such as a
    ``NullReferenceException`` for a Hue answer of ``{"data":[null]}``. The port raises this instead, so that its callers
    fail as the app's did: ``dotnet_type`` names .NET's exception, and the message is .NET's."""

    def __init__(self, dotnet_type: str, message: str) -> None:
        super().__init__(message)
        self.dotnet_type = dotnet_type


NULL_REFERENCE = "Object reference not set to an instance of an object."


def null_reference() -> UnexpectedError:
    return UnexpectedError("NullReferenceException", NULL_REFERENCE)


def argument_null(parameter: str) -> UnexpectedError:
    return UnexpectedError("ArgumentNullException", f"Value cannot be null. (Parameter '{parameter}')")
