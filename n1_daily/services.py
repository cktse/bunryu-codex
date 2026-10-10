"""Finite-timeout service adapters. Tests inject local fakes."""

import base64
import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class DefiniteFailure(Exception):
    """The email was not accepted; a later invocation may retry."""


class UncertainSend(Exception):
    """Acceptance cannot be determined; automatic resend is unsafe."""


class ServiceUnavailable(Exception):
    """A generation request can be retried without using a correction."""


class InvalidResponse(Exception):
    """A completed model response does not contain the required JSON."""


def post_json(url, payload, headers, timeout):
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", **headers}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


class GmailSender:
    def __init__(self, client_id, client_secret, refresh_token, timeout=30):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.timeout = timeout

    def send(self, payload):
        body = urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                          "refresh_token": self.refresh_token, "grant_type": "refresh_token"}).encode()
        request = Request("https://oauth2.googleapis.com/token", data=body,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                token = json.load(response)["access_token"]
            if not isinstance(token, str) or not token.strip():
                raise ValueError("OAuth response contained no usable access token")
        except (OSError, HTTPException, ValueError, KeyError, TypeError) as error:
            if isinstance(error, HTTPError):
                error.close()
            raise DefiniteFailure("OAuth refresh failed before Gmail send") from error
        try:
            result = post_json("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                               {"raw": base64.urlsafe_b64encode(payload).decode("ascii")},
                               {"Authorization": "Bearer " + token}, self.timeout)
        except HTTPError as error:
            error.close()
            if error.code >= 500 or error.code == 408:
                raise UncertainSend(f"Gmail returned HTTP {error.code}; check the sending mailbox") from error
            raise DefiniteFailure(f"Gmail rejected the request: HTTP {error.code}") from error
        except (OSError, HTTPException, ValueError) as error:
            raise UncertainSend("Gmail response lost or unreadable; check the sending mailbox") from error
        if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not result["id"]:
            raise UncertainSend("Gmail response has no message ID; check the sending mailbox")
        return result["id"]
