"""Domain errors. Each carries the HTTP status the API answers with; messages are shown to users as they are."""


class QuantLabError(Exception):
    status = 500


class InvalidInput(QuantLabError, ValueError):
    status = 400


class NotFound(QuantLabError, LookupError):
    status = 404


class Conflict(QuantLabError, ValueError):
    status = 409


class DataSourceError(QuantLabError):
    """The market data provider failed or could not be reached."""
    status = 502
