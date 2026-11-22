import pytest

from fluxftp.trade_config import load_config, match_irc_message, match_name, validate_config


def sample_config():
    return {
        'trading': {'enabled': False, 'poll_seconds': 10, 'stable_polls': 3},
        'sources': [{'id': 'incoming', 'bookmark': 'siteA', 'path': '/incoming',
                     'whitelist': ['*'], 'blacklist': ['*.partial'],
                     'completion_marker': '.done'}],
        'destinations': [{'id': 'archive', 'bookmark': 'siteB', 'path': '/archive',
                          'conflict': 'rename', 'verify': 'size'}],
        'routes': [{'id': 'routeAB', 'source': 'incoming', 'destination': 'archive'}],
        'irc': {
            'networks': [{'id': 'trades', 'host': 'irc.example.net', 'nickname': 'bot',
                          'channels': ['#trades']}],
            'triggers': [{'id': 'release', 'network': 'trades', 'route': 'routeAB',
                          'channels': ['#trades'], 'senders': ['releasebot'],
                          'pattern': r'^!trade (?P<folder>[A-Za-z0-9][A-Za-z0-9._ -]{0,119})$'}],
        },
    }


def test_validates_routes_filters_irc_and_destination_policy():
    config = validate_config(sample_config(), {'siteA', 'siteB'})

    assert config['enabled'] is False
    assert config['poll_seconds'] == 10
    assert config['destinations'][0]['conflict'] == 'rename'
    assert config['destinations'][0]['verify'] == 'size'
    assert config['sources'][0]['completion_marker'] == '.done'
    assert match_name('Show.Release', config['sources'][0]['whitelist'],
                      config['sources'][0]['blacklist'])
    assert not match_name('bad.partial', config['sources'][0]['whitelist'],
                          config['sources'][0]['blacklist'])


def test_irc_requires_allowlisted_network_channel_sender_and_bounded_folder():
    config = validate_config(sample_config(), {'siteA', 'siteB'})

    assert match_irc_message(config, 'trades', '#trades', 'releasebot', '!trade Movie.2026') == {
        'trigger_id': 'release', 'route_id': 'routeAB', 'folder': 'Movie.2026'}
    assert match_irc_message(config, 'other', '#trades', 'releasebot', '!trade Movie') is None
    assert match_irc_message(config, 'trades', '#other', 'releasebot', '!trade Movie') is None
    assert match_irc_message(config, 'trades', '#trades', 'stranger', '!trade Movie') is None
    assert match_irc_message(config, 'trades', '#trades', 'releasebot', '!trade ../secret') is None
    assert match_irc_message(config, 'trades', '#trades', 'releasebot', 'x' * 2049) is None


@pytest.mark.parametrize('mutation, message', [
    (lambda c: c.update(extra=True), 'unknown key'),
    (lambda c: c['sources'][0].update(path='/incoming/../elsewhere'), 'normalized absolute'),
    (lambda c: c['routes'][0].update(destination='missing'), 'unknown source or destination'),
    (lambda c: c['destinations'][0].update(conflict='delete'), 'must be fail, skip, or rename'),
    (lambda c: c['irc']['triggers'][0].update(pattern=r'(?P<folder>(a+)+)'), 'simple character class'),
])
def test_rejects_invalid_rules(mutation, message):
    config = sample_config()
    mutation(config)

    with pytest.raises(ValueError, match=message):
        validate_config(config, {'siteA', 'siteB'})


def test_missing_file_uses_safe_disabled_defaults(tmp_path):
    config = load_config(tmp_path / '.fluxftprc', {'siteA'})

    assert config['enabled'] is False
    assert config['sources'] == []
    assert config['triggers'] == []
