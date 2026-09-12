from __future__ import unicode_literals


RESTORE_SET_LABEL_IDS = {
    'addons': 30030,
    'addon_data': 30031,
    'database': 30032,
    'game_saves': 30133,
    'playlists': 30033,
    'profiles': 30080,
    'thumbnails': 30034,
    'config': 30035,
    'skin_config': 30234,
}

RESTORE_PREPARATION_PHASE_IDS = {
    'copy_archive': 30232,
    'verify_archive': 30233,
    'extract_archive': 30100,
}


def restore_set_labels(set_ids, localize):
    """Return display labels without changing the archive's internal ids."""
    return [
        localize(RESTORE_SET_LABEL_IDS[set_id.casefold()])
        if set_id.casefold() in RESTORE_SET_LABEL_IDS else set_id
        for set_id in set_ids
    ]


def selected_restore_set_ids(set_ids, selected_indexes):
    """Map GUI indexes back to the original manifest identifiers."""
    return [set_ids[index] for index in selected_indexes]


def restore_preparation_message(localize, phase=None):
    """Describe read-only preparation before files are restored to Kodi."""
    lines = [localize(30231)]
    if phase is not None:
        lines.append(localize(RESTORE_PREPARATION_PHASE_IDS[phase]))
    return '\n'.join(lines)
