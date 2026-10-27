"""Mouse action preferences shared by the options page and event handlers."""

DOUBLE_CLICK_DEFAULTS = {
    'ftp_file': '(None)',
    'local_file': 'Execute File',
    'log': 'Max/Restore Window',
    'site': 'Show Site property',
    'indicator': '(None)',
    'tab': '(None)',
    'empty_tabs': '(None)',
    'queue_item': '(None)',
}

CHOICES = {
    'ftp_file': ['(None)', 'Begin Transfer', 'Add to Queue', 'Edit File', 'View File'],
    'local_file': ['(None)', 'Begin Transfer', 'Add to Queue', 'Execute File'],
    'log': ['Max/Restore Window', 'View log at new window'],
    'site': ['Show Site property', 'Connect to Site'],
    'indicator': ['(None)'],
    'tab': ['(None)'],
    'empty_tabs': ['(None)', 'Create New Window'],
    'queue_item': ['(None)', 'Enter Source Folder', 'Edit Queue'],
    'explorer_drop': ['Begin Transfer', 'Add to Queue'],
    'local_context': ['Default popup menu', 'Explorer Menu'],
}


def mouse_action(settings, key):
    default = DOUBLE_CLICK_DEFAULTS.get(key, CHOICES[key][0])
    value = settings.value('Mouse/' + key, default, type=str)
    if key == 'log' and value == 'Max/Restore window':
        value = 'Max/Restore Window'
    return value if value in CHOICES[key] else default

DOUBLE_CLICK_LABELS = {
    'ftp_file': 'At a FTP File',
    'local_file': 'At a Local File',
    'log': 'at Log Window',
    'site': 'at Site of Sites Manager',
    'indicator': 'at Side Indicator Panel',
    'tab': 'at Tab',
    'empty_tabs': 'at Empty Area of Tabs',
    'queue_item': 'at Queue Item',
}
