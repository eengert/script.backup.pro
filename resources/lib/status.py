from __future__ import unicode_literals


def describe_status(facts):
    """Reduce raw, already-gathered status facts into an ordered list of
    (label_key, detail) pairs for display. Kept Kodi-independent so it is
    directly unit-testable; a caller resolves each label_key into a
    localized string and appends detail (when present) to build the text
    shown on screen.

    Never mutates anything and never performs I/O itself - callers are
    responsible for gathering `facts` cheaply (local settings/files only)
    before calling this.
    """
    lines = []

    last_backup = facts.get('last_backup', {})
    if last_backup.get('known'):
        lines.append(('last_backup_known', last_backup.get('label', '')))
    else:
        lines.append(('last_backup_unknown', None))

    remote = facts.get('remote', {})
    if remote.get('configured'):
        lines.append(('remote_configured', remote.get('label', '')))
    else:
        lines.append(('remote_not_configured', None))

    recovery_pending = bool(facts.get('recovery_pending'))
    lines.append(
        ('recovery_pending', None) if recovery_pending
        else ('recovery_clear', None))

    scheduler = facts.get('scheduler', {})
    if scheduler.get('enabled'):
        if scheduler.get('next_run_label'):
            lines.append(
                ('scheduler_enabled_next', scheduler['next_run_label']))
        else:
            lines.append(('scheduler_enabled_unknown', None))
    else:
        lines.append(('scheduler_disabled', None))

    # a pending recovery is put first so it can't be missed at a glance,
    # without changing the relative order of everything else
    if recovery_pending:
        pending_line = lines.pop(2)
        lines.insert(0, pending_line)

    return lines
