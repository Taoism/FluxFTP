"""Choose the passive endpoint without silently overriding explicit site roles."""
from .site_options import decode


def source_passive(source, destination):
    left = decode(source.site_options).get('Transfer/fxp_role', 'Auto')
    right = decode(destination.site_options).get('Transfer/fxp_role', 'Auto')
    if left == right and left != 'Auto':
        raise ValueError('FXP sites cannot both be ' + left.lower() + '. Choose complementary roles or Auto.')
    return left == 'Passive' or right == 'Active'


def source_passive_candidates(source, destination):
    """Try the usual destination-passive order first, then reverse if both sites are Auto.

    The alternate is attempted only while preparing the data route, before RETR/STOR
    are issued. Explicit per-site roles remain authoritative.
    """
    left = decode(source.site_options).get('Transfer/fxp_role', 'Auto')
    right = decode(destination.site_options).get('Transfer/fxp_role', 'Auto')
    preferred = source_passive(source, destination)
    return (False, True) if left == right == 'Auto' else (preferred,)
