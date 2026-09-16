import unittest

from resources.lib.remote_diagnostics import (
    describe_remote_destination,
    redact_uri,
    sanitize_dropbox_destination,
    sanitize_path_destination,
)

SMB_WITH_CREDENTIALS = (
    'smb://exampleuser:example-password@192.0.2.10:445/AppleTv/backups/'
    'ExampleRoom - Main/')
SMB_NO_CREDENTIALS = 'smb://192.0.2.10/AppleTv/backups/ExampleRoom - Main/'


class SanitizePathDestinationTests(unittest.TestCase):
    def test_smb_with_credentials_reports_presence_without_either_secret(self):
        result = sanitize_path_destination(SMB_WITH_CREDENTIALS)

        self.assertTrue(result['credentials_present'])
        self.assertTrue(result['username_present'])
        self.assertTrue(result['password_present'])
        self.assertEqual('smb', result['scheme'])
        self.assertEqual('192.0.2.10', result['host'])
        self.assertEqual('remote', result['classification'])
        # The whole point: no field anywhere carries the actual secrets.
        serialized = repr(result)
        self.assertNotIn('exampleuser', serialized)
        self.assertNotIn('example-password', serialized)

    def test_destination_with_no_credentials_reports_false(self):
        result = sanitize_path_destination(SMB_NO_CREDENTIALS)

        self.assertFalse(result['credentials_present'])
        self.assertFalse(result['username_present'])
        self.assertFalse(result['password_present'])
        self.assertEqual('192.0.2.10', result['host'])

    def test_username_only_is_distinguished_from_password_only(self):
        username_only = sanitize_path_destination(
            'smb://exampleuser@192.0.2.10/share/')
        password_only = sanitize_path_destination(
            'smb://:example-password@192.0.2.10/share/')

        self.assertTrue(username_only['username_present'])
        self.assertFalse(username_only['password_present'])
        self.assertTrue(username_only['credentials_present'])

        self.assertFalse(password_only['username_present'])
        self.assertTrue(password_only['password_present'])
        self.assertTrue(password_only['credentials_present'])

    def test_explicit_port_is_preserved_structurally(self):
        result = sanitize_path_destination(SMB_WITH_CREDENTIALS)

        self.assertEqual(445, result['port'])
        self.assertTrue(result['port_present'])

    def test_missing_port_is_reported_absent_not_a_default(self):
        result = sanitize_path_destination(SMB_NO_CREDENTIALS)

        self.assertIsNone(result['port'])
        self.assertFalse(result['port_present'])

    def test_percent_encoded_path_is_handled_without_raising(self):
        result = sanitize_path_destination(
            'smb://user:p%40ss@192.0.2.10/share/Family%20Room/')

        self.assertTrue(result['credentials_present'])
        self.assertEqual('192.0.2.10', result['host'])
        self.assertIsInstance(result['path'], str)
        serialized = repr(result)
        self.assertNotIn('p%40ss', serialized)
        self.assertNotIn('p@ss', serialized)

    def test_empty_destination_is_not_configured(self):
        for raw in ('', '   ', None):
            result = sanitize_path_destination(raw)
            self.assertFalse(result['configured'])
            self.assertFalse(result['credentials_present'])

    def test_local_path_is_classified_local_with_no_host_or_credentials(self):
        result = sanitize_path_destination('/Users/example/backups/')

        self.assertEqual('local', result['classification'])
        self.assertIsNone(result['host'])
        self.assertFalse(result['credentials_present'])

    def test_windows_drive_letter_is_not_misread_as_a_network_scheme(self):
        result = sanitize_path_destination('C:/Users/example/backups/')

        self.assertEqual('local', result['classification'])
        self.assertIsNone(result['host'])
        self.assertFalse(result['credentials_present'])

    def test_malformed_value_reports_configured_without_raising(self):
        # bracketed IPv6-shaped junk without a closing bracket is a
        # classic urlsplit ValueError trigger
        result = sanitize_path_destination('smb://[::1/share/')

        self.assertTrue(result['configured'])
        self.assertEqual('unknown', result['classification'])
        self.assertFalse(result['credentials_present'])


class SanitizeDropboxDestinationTests(unittest.TestCase):
    def test_both_credentials_present(self):
        result = sanitize_dropbox_destination('app-key-123', 'app-secret-456')

        self.assertTrue(result['credentials_present'])
        self.assertTrue(result['username_present'])
        self.assertTrue(result['password_present'])
        serialized = repr(result)
        self.assertNotIn('app-key-123', serialized)
        self.assertNotIn('app-secret-456', serialized)

    def test_no_credentials_configured(self):
        result = sanitize_dropbox_destination('', '')

        self.assertFalse(result['configured'])
        self.assertFalse(result['credentials_present'])


class DescribeRemoteDestinationTests(unittest.TestCase):
    def test_primary_slot_selected(self):
        result = describe_remote_destination(
            0, SMB_WITH_CREDENTIALS, '', '', '')
        self.assertEqual('primary_path', result['slot'])
        self.assertTrue(result['credentials_present'])

    def test_secondary_slot_selected(self):
        result = describe_remote_destination(
            1, SMB_WITH_CREDENTIALS, SMB_NO_CREDENTIALS, '', '')
        self.assertEqual('secondary_path', result['slot'])
        self.assertFalse(result['credentials_present'])

    def test_dropbox_slot_selected(self):
        result = describe_remote_destination(2, '', '', 'key', 'secret')
        self.assertEqual('dropbox', result['slot'])
        self.assertTrue(result['credentials_present'])


class RedactUriTests(unittest.TestCase):
    def test_strips_username_and_password_from_display_string(self):
        redacted = redact_uri(SMB_WITH_CREDENTIALS)

        self.assertNotIn('exampleuser', redacted)
        self.assertNotIn('example-password', redacted)
        self.assertIn('192.0.2.10', redacted)
        self.assertIn('ExampleRoom - Main', redacted)

    def test_leaves_credential_free_value_unchanged(self):
        self.assertEqual(SMB_NO_CREDENTIALS, redact_uri(SMB_NO_CREDENTIALS))

    def test_leaves_local_path_unchanged(self):
        local = '/Users/example/backups/20260910120000.zip'
        self.assertEqual(local, redact_uri(local))

    def test_handles_empty_and_none(self):
        self.assertEqual('', redact_uri(''))
        self.assertIsNone(redact_uri(None))

    def test_preserves_port_when_stripping_credentials(self):
        redacted = redact_uri(SMB_WITH_CREDENTIALS)
        self.assertIn(':445', redacted)

    def test_strips_query_and_fragment_alongside_credentials(self):
        # No destination Backup Pro currently supports puts a secret in a
        # query string or fragment, but a value that already carries
        # embedded credentials is exactly the case where an unknown
        # provider might - drop both defensively once we know we're
        # already rewriting the value.
        redacted = redact_uri(
            'smb://user:pass@192.0.2.10/share/?token=SECRETTOKEN#frag')
        self.assertNotIn('SECRETTOKEN', redacted)
        self.assertNotIn('frag', redacted)
        self.assertNotIn('user', redacted)
        self.assertNotIn('pass', redacted)
        self.assertEqual('smb://192.0.2.10/share/', redacted)

    def test_preserves_query_when_no_credentials_present(self):
        # Only rewrite the value when there is actually something to
        # redact - a credential-free query string is left exactly alone.
        unchanged = 'smb://192.0.2.10/share/?token=NOT_A_SECRET'
        self.assertEqual(unchanged, redact_uri(unchanged))


if __name__ == '__main__':
    unittest.main()
