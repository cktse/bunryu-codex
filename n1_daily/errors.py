class InputError(ValueError):
    """Invalid local input or authoritative storage."""


class StorageError(InputError):
    """Invalid authoritative storage, distinct from service setup."""


class ServiceError(Exception):
    """A retryable service/setup failure."""


class ModelOutputError(Exception):
    """A completed request that did not supply usable content."""


class DeliveryError(ServiceError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain
