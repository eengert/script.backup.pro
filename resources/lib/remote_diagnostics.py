"""Sanitized, credential-free structural diagnostics for the configured
backup/restore remote destination.

This module exists to make an intermittent remote-transfer failure (e.g.
an Apple TV SMB destination) diagnosable from Backup Pro's own logs
without ever writing a username, password, token, or complete
credential-bearing URI anywhere - not to xbmc.log, not to the manifest,
not to a window property, not to durable state, and not into an
exception/dialog message. Every function here returns only booleans and
non-sensitive structure (scheme, host, port, a credential-free path) -
never a raw setting value.

Two complementary helpers are provided:
- `sanitize_path_destination()` / `sanitize_dropbox_destination()` /
  `describe_remote_destination()` build the structured, JSON-safe
  diagnostic dict new call sites can log directly.
- `redact_uri()` strips only an embedded `user:pass@` credential prefix
  from an existing display string, for narrowly fixing a call site that
  already logs/shows a human-readable path and must keep doing so, minus
  the secret.
"""
from __future__ import unicode_literals

try:
    from urllib.parse import urlsplit
except ImportError:  # pragma: no cover - Kodi's Python 3 always has this
    from urlparse import urlsplit


# Schemes Kodi's VFS treats as network destinations. Anything else -
# including a bare local path, or a Windows drive letter like "C:" that
# urlsplit would otherwise misparse as a one-letter "scheme" - is reported
# as local, never as a host/credential-bearing destination.
_NETWORK_SCHEMES = frozenset({
    'smb', 'nfs', 'ftp', 'ftps', 'sftp', 'afp', 'dav', 'davs', 'upnp',
})

_EMPTY_DESTINATION = {
    'configured': False,
    'scheme': None,
    'classification': 'local',
    'host': None,
    'port': None,
    'port_present': False,
    'path': None,
    'username_present': False,
    'password_present': False,
    'credentials_present': False,
}

_UNKNOWN_DESTINATION = {
    'configured': True,
    'scheme': None,
    'classification': 'unknown',
    'host': None,
    'port': None,
    'port_present': False,
    'path': None,
    'username_present': False,
    'password_present': False,
    'credentials_present': False,
}


def sanitize_path_destination(raw_value):
    """Sanitize a path-based destination (Backup Pro's `remote_path` or
    `remote_path_2` setting value) into structural, credential-free
    diagnostic fields.

    Never returns the raw value, embedded credentials, or the URI's query
    string/fragment (some VFS providers pack tokens there) - only scheme,
    host, port, a credential-free path, and presence booleans.
    """
    raw_value = raw_value or ''
    if not raw_value.strip():
        return dict(_EMPTY_DESTINATION)

    try:
        parts = urlsplit(raw_value)
    except ValueError:
        # Malformed value - report only that something is configured;
        # never echo the raw string back into a log.
        return dict(_UNKNOWN_DESTINATION)

    scheme = (parts.scheme or '').lower()
    is_network = scheme in _NETWORK_SCHEMES or (
        not scheme and bool(parts.netloc))
    username_present = bool(parts.username)
    password_present = bool(parts.password)
    try:
        port = parts.port if is_network else None
    except ValueError:
        port = None

    return {
        'configured': True,
        'scheme': scheme if (is_network and scheme) else None,
        'classification': 'remote' if is_network else 'local',
        'host': parts.hostname if is_network else None,
        'port': port,
        'port_present': port is not None,
        'path': (parts.path or '/') if is_network else None,
        'username_present': username_present,
        'password_present': password_present,
        'credentials_present': username_present or password_present,
    }


def sanitize_dropbox_destination(app_key, app_secret):
    """Sanitize the Dropbox destination's app-credential presence.

    `app_key`/`app_secret` are never returned - only whether each is
    configured, matching the structural shape of
    `sanitize_path_destination()` so callers can log either uniformly.
    """
    key_present = bool((app_key or '').strip())
    secret_present = bool((app_secret or '').strip())
    return {
        'configured': key_present or secret_present,
        'scheme': 'dropbox',
        'classification': 'remote',
        'host': None,
        'port': None,
        'port_present': False,
        'path': None,
        'username_present': key_present,
        'password_present': secret_present,
        'credentials_present': key_present or secret_present,
    }


def describe_remote_destination(remote_selection, remote_path, remote_path_2,
                                 dropbox_key, dropbox_secret):
    """Sanitize whichever destination slot `remote_selection` selects.

    `remote_selection` follows Backup Pro's own setting values: 0 =
    primary path, 1 = secondary path, 2 = Dropbox. The returned dict adds
    a `slot` field identifying which one was used, on top of the fields
    `sanitize_path_destination()`/`sanitize_dropbox_destination()` return.
    """
    if int(remote_selection) == 1:
        result = sanitize_path_destination(remote_path_2)
        result['slot'] = 'secondary_path'
    elif int(remote_selection) == 2:
        result = sanitize_dropbox_destination(dropbox_key, dropbox_secret)
        result['slot'] = 'dropbox'
    else:
        result = sanitize_path_destination(remote_path)
        result['slot'] = 'primary_path'
    return result


def redact_uri(raw_value):
    """Return `raw_value` with any embedded `username:password@` prefix
    removed, or unchanged if it carries none.

    For narrowly fixing an existing log/dialog call site that already
    displays a human-readable path and must keep doing so, minus the
    secret - unlike `sanitize_path_destination()`, this does not reduce
    the value to structural fields. Never raises: a value `urlsplit`
    cannot parse is returned unchanged rather than risking a leak through
    a partial/incorrect reconstruction.
    """
    if not raw_value:
        return raw_value
    try:
        parts = urlsplit(raw_value)
    except ValueError:
        return raw_value
    if not (parts.username or parts.password):
        return raw_value
    host = parts.hostname or ''
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host if port is None else '%s:%s' % (host, port)
    try:
        return parts._replace(netloc=netloc).geturl()
    except ValueError:
        return raw_value
