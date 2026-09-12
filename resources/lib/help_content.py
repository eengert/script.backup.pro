from __future__ import unicode_literals

# Organized Help content as (header_string_id, [body_string_id, ...])
# sections, in display order. Kept Kodi-independent - like
# status.py's describe_status() - so the structure and every string id
# it references can be verified without executing default.py or
# stubbing the Kodi APIs it needs at import time.
HELP_SECTIONS = [
    (30196, [30197, 30198, 30199]),          # Overview
    (30200, [30201, 30202, 30203, 30204, 30205, 30206]),  # What gets backed up
    (30207, [30208, 30209, 30228]),           # Compression
    (30210, [30211]),                        # Destination / storage
    (30212, [30213, 30214, 30215, 30216]),   # Restore
    (30217, [30218]),                        # Scheduling
    (30219, [30220, 30221]),                 # Status
    (30222, [30223, 30224]),                 # Warnings and failures
    (30225, [30226, 30227]),                 # Safety / limitations
]


def build_help_text(resolve):
    """Compose the full Help body text from HELP_SECTIONS.

    `resolve` is a callable mapping a string id to its localized text
    (normally utils.getString) - supplied by the caller so this stays
    Kodi-independent and directly unit-testable with a plain stub.
    """
    sections = []
    for header_id, body_ids in HELP_SECTIONS:
        header = resolve(header_id)
        body = '\n\n'.join(resolve(body_id) for body_id in body_ids)
        sections.append('%s\n%s' % (header, body))
    return '\n\n\n'.join(sections)
