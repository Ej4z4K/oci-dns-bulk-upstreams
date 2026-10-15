"""Load non-secret migration inputs. CLI flags override JSON; JSON paths are file-relative."""
import json
from pathlib import Path
import time
import oci

CONFIG_KEYS = {'compartment_id', 'source_ip', 'target_ip', 'profile', 'region',
               'zone_ids_file', 'zone_names_file', 'name_prefix', 'expected_count',
               'output_dir', 'api_delay', 'verification'}
PATH_KEYS = {'zone_ids_file', 'zone_names_file', 'output_dir'}
ALIASES = {'source_ip': 'old_ip', 'target_ip': 'new_ip'}
VERIFY_KEYS = {'marker', 'record_name', 'logs_file', 'transfer_since', 'transfer_ips',
               'allow_other_upstreams'}


def load_options(args, defaults=None):
    settings = {}
    if getattr(args, 'config', None):
        path = Path(args.config).expanduser().resolve()
        settings = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(settings, dict) or settings.keys() - CONFIG_KEYS:
            raise ValueError('Migration config must be an object with documented keys only')
        if 'verification' in settings:
            verification = settings['verification']
            if not isinstance(verification, dict) or verification.keys() - VERIFY_KEYS:
                raise ValueError('Unknown verification config keys')
        for key in PATH_KEYS:
            if settings.get(key):
                value = Path(settings[key]).expanduser()
                settings[key] = value if value.is_absolute() else path.parent / value
        verification = dict(settings.get('verification', {}))
        if verification.get('logs_file'):
            value = Path(verification['logs_file']).expanduser()
            verification['logs_file'] = value if value.is_absolute() else path.parent / value
        settings.update(verification)
    for key, value in settings.items():
        destination = ALIASES.get(key, key)
        if hasattr(args, destination) and getattr(args, destination) is None:
            setattr(args, destination, value)
    for key, value in (defaults or {}).items():
        if hasattr(args, key) and getattr(args, key) is None:
            setattr(args, key, value)
    # JSON types are checked before API use; booleans must never be accepted as counts.
    count = getattr(args, 'expected_count', None)
    if count is not None and (type(count) is not int or count < 1):
        raise ValueError('expected_count must be a positive integer')
    delay = getattr(args, 'api_delay', 0.6)
    if type(delay) not in (int, float) or not 0 <= delay <= 60:
        raise ValueError('api_delay must be a number from 0 to 60 seconds')
    for key in ('old_ip', 'new_ip', 'profile', 'region', 'compartment_id', 'name_prefix', 'marker', 'record_name'):
        value = getattr(args, key, None)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(key + ' must be a nonempty string')
    return args


def sdk_config(args):
    config = oci.config.from_file(profile_name=args.profile)
    # Omitted --region uses the selected OCI profile, not a fixed UK region.
    if args.region:
        config['region'] = args.region
    return config


class PacedDnsClient:
    """Serialize API calls with a caller-selected delay and SDK retries for transient errors."""
    def __init__(self, config, delay=0.6):
        self.client = oci.dns.DnsClient(config, timeout=(10, 60),
                                       retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        self.delay = delay

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if not callable(method):
            return method
        def call(*args, **kwargs):
            time.sleep(self.delay)
            return method(*args, **kwargs)
        return call
