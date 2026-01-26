"""
Configuration loader for DrawIO Browser.
Reads settings from settings.ini file.
"""

import os
import configparser
from pathlib import Path


def find_settings_file():
    """Find settings.ini file, searching up from current directory."""
    # Check current directory first
    if os.path.exists('settings.ini'):
        return 'settings.ini'

    # Check in parent directories up to 3 levels
    current = Path.cwd()
    for _ in range(3):
        settings_path = current / 'settings.ini'
        if settings_path.exists():
            return str(settings_path)
        current = current.parent

    # Check in package directory
    package_dir = Path(__file__).parent.parent
    settings_path = package_dir / 'settings.ini'
    if settings_path.exists():
        return str(settings_path)

    return None


def load_settings(settings_path=None):
    """
    Load settings from INI file.
    Returns a dict with all configuration values.
    """
    if settings_path is None:
        settings_path = find_settings_file()

    if settings_path is None or not os.path.exists(settings_path):
        raise FileNotFoundError(
            "settings.ini not found. Copy settings.ini.example to settings.ini "
            "and configure your Confluence details."
        )

    config = configparser.ConfigParser()
    config.read(settings_path)

    # Get base directory (where settings.ini is located)
    base_dir = os.path.dirname(os.path.abspath(settings_path))

    def resolve_path(path):
        """Resolve relative paths against base directory."""
        if path.startswith('./') or path.startswith('../'):
            return os.path.normpath(os.path.join(base_dir, path))
        return path

    settings = {
        # Confluence settings
        'confluence_url': config.get('Confluence', 'url', fallback=''),
        'confluence_username': config.get('Confluence', 'username', fallback=''),
        'confluence_password': config.get('Confluence', 'password', fallback=''),
        'confluence_spaces': [
            s.strip() for s in config.get('Confluence', 'spaces', fallback='').split(',')
            if s.strip()
        ],

        # Local paths
        'content_directory': resolve_path(
            config.get('Local', 'content_directory', fallback='./data/content')
        ),
        'database_path': resolve_path(
            config.get('Local', 'database_path', fallback='./data/diagrams.db')
        ),
        'index_directory': resolve_path(
            config.get('Local', 'index_directory', fallback='./data/whoosh_index')
        ),

        # Browser settings
        'host': config.get('Browser', 'host', fallback='127.0.0.1'),
        'port': config.getint('Browser', 'port', fallback=5000),
        'debug': config.getboolean('Browser', 'debug', fallback=False),
        'show_edit_buttons': config.getboolean('Browser', 'show_edit_buttons', fallback=True),

        # Extractor settings
        'rate_limit': config.getint('Extractor', 'rate_limit', fallback=5),
        'batch_size': config.getint('Extractor', 'batch_size', fallback=50),
        'skip_personal_spaces': config.getboolean('Extractor', 'skip_personal_spaces', fallback=True),
    }

    # Derive subdirectories
    content_dir = settings['content_directory']
    settings['diagrams_directory'] = os.path.join(content_dir, 'diagrams')
    settings['images_directory'] = os.path.join(content_dir, 'images')
    settings['metadata_directory'] = os.path.join(content_dir, 'metadata')

    return settings


class Settings:
    """Singleton-like settings object for easy access."""
    _instance = None
    _settings = None

    @classmethod
    def get(cls, key=None, settings_path=None):
        """Get settings value or all settings."""
        if cls._settings is None:
            cls._settings = load_settings(settings_path)

        if key is None:
            return cls._settings
        return cls._settings.get(key)

    @classmethod
    def reload(cls, settings_path=None):
        """Reload settings from file."""
        cls._settings = load_settings(settings_path)
        return cls._settings
