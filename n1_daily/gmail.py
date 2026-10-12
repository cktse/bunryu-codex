import base64
from urllib.parse import urlencode

from .errors import DeliveryError, ServiceError
from .http import details, request_json


class Sender:
    def __init__(self, config, secrets):
        self.config, self.secrets = config, secrets
        self._access_token = None

    def prepare(self):
        self.config.require('google_client_id')
        credentials = self.secrets.require('google_client_secret', 'google_refresh_token')
        payload = urlencode({'client_id': self.config.google_client_id,
                             'client_secret': credentials['google_client_secret'],
                             'refresh_token': credentials['google_refresh_token'], 'grant_type': 'refresh_token'}).encode()
        result = request_json('OAuth refresh', 'https://oauth2.googleapis.com/token', payload,
                              {'Content-Type': 'application/x-www-form-urlencoded'},
                              self.config.timeout_seconds, self.secrets.redactions())
        token = result.get('access_token')
        if not isinstance(token, str) or not token.strip():
            raise ServiceError('OAuth refresh: no usable access token; ' + details(result, self.secrets.redactions()))
        self._access_token = token

    def send(self, message):
        result = request_json('Gmail send', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                              {'raw': base64.urlsafe_b64encode(message).decode()},
                              {'Authorization': 'Bearer ' + self._access_token}, self.config.timeout_seconds,
                              (*self.secrets.redactions(), self._access_token), sending=True)
        identity = result.get('id')
        if not isinstance(identity, str) or not identity.strip():
            raise DeliveryError('Gmail send: acceptance response has no usable message ID; ' +
                                details(result, (*self.secrets.redactions(), self._access_token)), uncertain=True)
        return identity
