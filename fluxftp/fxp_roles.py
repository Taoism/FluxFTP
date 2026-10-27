"""Choose the passive endpoint without silently overriding explicit site roles."""
from .site_options import decode


def source_passive(source, destination):
    left = decode(source.site_options).get('Transfer/fxp_role', 'Auto')
    right = decode(destination.site_options).get('Transfer/fxp_role', 'Auto')
    if left == right and left != 'Auto':
        raise ValueError('FXP sites cannot both be ' + left.lower() + '. Choose complementary roles or Auto.')
    return left == 'Passive' or right == 'Active'
