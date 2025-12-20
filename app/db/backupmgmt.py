"""Database backup and restore management for CacheInfinity."""

from __future__ import annotations

import json
import logging
import yaml
from pathlib import Path
from typing import Optional, Tuple, List, TYPE_CHECKING

from core.errors import ConfigError

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from db.schema import IndexDatabase

class ConfigExportError(Exception):
    """Raised when configuration export fails."""
    pass

class ConfigImportError(Exception):
    """Raised when configuration import fails."""
    pass

class DatabaseBackupManager:
    """Manages database backup and restore operations using bootstrap.yml."""

    def __init__(self, index_db: 'IndexDatabase', config_dir: Path):
        """Initialize backup manager with IndexDatabase.

        Args:
            index_db: IndexDatabase instance for database operations
            config_dir: Path to the configuration directory
        """
        self.index_db = index_db
        self.config_dir = config_dir
        self._logger = logging.getLogger(__name__)

    def export_config_to_yaml(self, output_path: Path) -> Path:
        """Export all configuration from database to bootstrap.yml.

        Args:
            output_path: Path to save bootstrap.yml file

        Returns:
            Path to the saved bootstrap.yml file

        Raises:
            ConfigExportError: If export fails completely
        """
        try:
            # Collect all configuration from database
            bootstrap_data = self._collect_all_configuration()

            # Validate no database configuration is present
            self._validate_no_database_config(bootstrap_data)

            # Save to bootstrap.yml
            with output_path.open('w', encoding='utf-8') as f:
                yaml.safe_dump(bootstrap_data, f, default_flow_style=False, indent=2)

            self._logger.info(f"Configuration exported to {output_path}")
            return output_path

        except Exception as exc:
            raise ConfigExportError(f"Configuration export failed: {exc}")

    def import_config_from_yaml(self, bootstrap_path: Path) -> Tuple[bool, List[str]]:
        """Import configuration from bootstrap.yml to database.

        Args:
            bootstrap_path: Path to bootstrap.yml file

        Returns:
            Tuple of (success: bool, warnings: list[str])
            - success: True if any valid data was processed
            - warnings: List of warning messages for invalid data

        Raises:
            ConfigImportError: If no valid data was found or critical errors occurred
        """
        warnings = []
        valid_data_found = False

        try:
            # Parse YAML file
            with bootstrap_path.open('r', encoding='utf-8') as f:
                bootstrap_data = yaml.safe_load(f) or {}

            # Validate no database configuration is present
            db_warnings = self._validate_no_database_config(bootstrap_data, log_only=True)
            warnings.extend(db_warnings)

            # Process each configuration section
            if 'paths' in bootstrap_data:
                paths_valid, paths_warnings = self._process_paths(bootstrap_data['paths'])
                if paths_valid:
                    valid_data_found = True
                warnings.extend(paths_warnings)

            if 'limits' in bootstrap_data:
                limits_valid, limits_warnings = self._process_limits(bootstrap_data['limits'])
                if limits_valid:
                    valid_data_found = True
                warnings.extend(limits_warnings)

            if 'indexing' in bootstrap_data:
                indexing_valid, indexing_warnings = self._process_indexing(bootstrap_data['indexing'])
                if indexing_valid:
                    valid_data_found = True
                warnings.extend(indexing_warnings)

            if 'auth' in bootstrap_data:
                auth_valid, auth_warnings = self._process_auth(bootstrap_data['auth'])
                if auth_valid:
                    valid_data_found = True
                warnings.extend(auth_warnings)

            if 'tls' in bootstrap_data:
                tls_valid, tls_warnings = self._process_tls(bootstrap_data['tls'])
                if tls_valid:
                    valid_data_found = True
                warnings.extend(tls_warnings)

            if 'cookies' in bootstrap_data:
                cookies_valid, cookies_warnings = self._process_cookies(bootstrap_data['cookies'])
                if cookies_valid:
                    valid_data_found = True
                warnings.extend(cookies_warnings)

            if 'webdav' in bootstrap_data:
                webdav_valid, webdav_warnings = self._process_webdav(bootstrap_data['webdav'])
                if webdav_valid:
                    valid_data_found = True
                warnings.extend(webdav_warnings)

            if 'users' in bootstrap_data:
                users_valid, users_warnings = self._process_users(bootstrap_data['users'])
                if users_valid:
                    valid_data_found = True
                warnings.extend(users_warnings)

            if 'cachelinks' in bootstrap_data:
                cachelinks_valid, cachelinks_warnings = self._process_cachelinks(bootstrap_data['cachelinks'])
                if cachelinks_valid:
                    valid_data_found = True
                warnings.extend(cachelinks_warnings)

            # Check if we processed any valid data
            if not valid_data_found:
                error_msg = "No valid configuration data found in bootstrap.yml"
                self._logger.error(error_msg)
                raise ConfigImportError(error_msg)

            # Log summary
            if warnings:
                self._logger.warning(f"Import completed with {len(warnings)} warnings")
            else:
                self._logger.info("Import completed successfully")

            return True, warnings

        except yaml.YAMLError as exc:
            error_msg = f"Invalid YAML syntax: {exc}"
            self._logger.error(error_msg)
            raise ConfigImportError(error_msg)
        except Exception as exc:
            error_msg = f"Configuration import failed: {exc}"
            self._logger.error(error_msg)
            raise ConfigImportError(error_msg)

    def _validate_no_database_config(self, data: dict, log_only: bool = False) -> List[str]:
        """Validate that no database configuration is present.

        Args:
            data: Configuration data to validate
            log_only: If True, return warnings instead of raising exception

        Returns:
            List of warning messages (if log_only=True)

        Raises:
            ConfigImportError: If database configuration is found and log_only=False
        """
        warnings = []
        forbidden_keys = {'database', 'db_type', 'db_user', 'db_password', 'database_url', 'postgres_dsn'}

        # Check top-level keys
        for key in forbidden_keys:
            if key in data:
                msg = f"Database configuration not allowed: {key}"
                if log_only:
                    warnings.append(msg)
                    self._logger.warning(msg)
                else:
                    self._logger.error(msg)
                    raise ConfigImportError(msg)

        # Check nested database configuration
        if 'database' in data:
            msg = "Database configuration section not allowed"
            if log_only:
                warnings.append(msg)
                self._logger.warning(msg)
            else:
                self._logger.error(msg)
                raise ConfigImportError(msg)

        return warnings

    def _collect_all_configuration(self) -> dict:
        """Collect all configuration from IndexDatabase.

        Returns:
            Dictionary containing all configuration in bootstrap.yml format
        """
        bootstrap_data = {}

        # Collect backends and staging
        paths = {}
        for backend in self.index_db.get_all_backends():
            paths[backend['name']] = {
                'backend_mounted': backend['backend_mounted'],
                'backend_cache_root': backend['backend_cache_root'],
                'backend_mount_root': backend['backend_mount_root']
            }

        staging = self.index_db.get_staging()
        if staging:
            paths['staging'] = {
                'staging_mounted': staging['staging_mounted'],
                'staging_mount_root': staging['staging_mount_root'],
                'size_gb': staging['size_gb']
            }

        bootstrap_data['paths'] = paths

        # Collect limits
        limits = self.index_db.get_limits()
        if limits:
            bootstrap_data['limits'] = {
                'max_zip_total_gb': limits['max_zip_total_gb'],
                'one_zip_cache_at_a_time': limits['one_zip_cache_at_a_time']
            }

        # Collect indexing settings
        indexing = self.index_db.get_indexing()
        if indexing:
            bootstrap_data['indexing'] = {
                'min_full_reindex_days': indexing['min_full_reindex_days'],
                'max_full_reindex_days': indexing['max_full_reindex_days'],
                'hot_window_days': indexing['hot_window_days'],
                'hot_radius': indexing['hot_radius'],
                'daily_full_reindex_budget': indexing['daily_full_reindex_budget'],
                'daily_cheap_check_budget': indexing['daily_cheap_check_budget'],
                'max_full_reindex_per_14d': indexing['max_full_reindex_per_14d'],
                'max_cheap_checks_per_day': indexing['max_cheap_checks_per_day'],
                'allow_early_full_on_change': indexing['allow_early_full_on_change'],
                'early_full_requires_hot': indexing['early_full_requires_hot'],
                'score_weights': json.loads(indexing['score_weights']) if indexing['score_weights'] else None
            }

        # Collect cookies - convert database format to bootstrap.yml format
        cookies = {}
        for cookie in self.index_db.get_all_cookies():
            # Write cookie content to file for bootstrap.yml format
            cookie_file_path = self.config_dir / "cookies" / f"{cookie['domain'].replace('.', '_')}.txt"
            cookie_file_path.parent.mkdir(parents=True, exist_ok=True)
            cookie_file_path.write_text(cookie['cookie_content'], encoding="utf-8")

            cookies[cookie['domain']] = {
                'cookie_jar': str(cookie_file_path),
                'credfile': str(cookie['credfile_path']) if cookie['credfile_path'] else None
            }

        bootstrap_data['cookies'] = cookies

        # Collect shares
        webdav = {}
        for share in self.index_db.get_all_shares():
            webdav[share['name']] = {
                'backend_folder': share['backend_folder'],
                'frontend_folder': share['frontend_folder'],
                'writable': share['writable'],
                'cachelink_overlay': share['cachelink_overlay'],
                'users': json.loads(share['users_config'])
            }

        bootstrap_data['webdav'] = webdav

        # Collect auth settings
        auth = self.index_db.get_auth()
        if auth:
            bootstrap_data['auth'] = {
                'oidc': json.loads(auth['oidc_config']) if auth['oidc_config'] else {},
                'ldap': json.loads(auth['ldap_config']) if auth['ldap_config'] else {},
                'proxy_header': json.loads(auth['proxy_config']) if auth['proxy_config'] else {}
            }

        # Collect TLS settings
        tls = self.index_db.get_tls()
        if tls:
            bootstrap_data['tls'] = {
                'enabled': tls['enabled'],
                'mode': tls['mode'],
                'manual': json.loads(tls['manual_config']) if tls['manual_config'] else {},
                'http': json.loads(tls['http_config']) if tls['http_config'] else {},
                'dns01': json.loads(tls['dns01_config']) if tls['dns01_config'] else {}
            }

        # Collect users
        users = {}
        for user in self.index_db.get_all_users():
            users[user['username']] = {
                'password_plain': user['password_plain'],
                'password_hash': user['password_hash'],
                'enabled': user['enabled'],
                'is_admin': user['is_admin'],
                'purpose': user['purpose']
            }

        bootstrap_data['users'] = users

        # Collect cachelinks
        cachelinks = []
        for cachelink in self.index_db.get_cachelinks():
            cachelinks.append({
                'canonical_id': cachelink['canonical_id'],
                'backend_path': cachelink['backend_path'],
                'url': cachelink['url'],
                'subfolder': cachelink['subfolder'],
                'mode': cachelink['mode'],
                'source_file': cachelink['source_file']
            })

        bootstrap_data['cachelinks'] = cachelinks

        return bootstrap_data

    def _process_paths(self, paths_data: dict) -> Tuple[bool, List[str]]:
        """Process paths configuration section with graceful error handling.

        Args:
            paths_data: Paths configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []
        success = False

        if not isinstance(paths_data, dict):
            warnings.append("Paths section must be a dictionary")
            return False, warnings

        # Process backends
        backends_processed = 0
        for name, backend_data in paths_data.items():
            if name.startswith('backend_'):
                try:
                    # Validate backend data
                    if not isinstance(backend_data, dict):
                        warnings.append(f"Backend {name} must be a dictionary")
                        continue

                    # Convert to database format
                    backend_db = {
                        'name': name,
                        'backend_mounted': bool(backend_data.get('backend_mounted', False)),
                        'backend_cache_root': str(backend_data.get('backend_cache_root', '')),
                        'backend_mount_root': str(backend_data.get('backend_mount_root')) if backend_data.get('backend_mount_root') else None
                    }

                    # Save to database
                    self.index_db.save_backend(backend_db)
                    backends_processed += 1

                except Exception as exc:
                    warnings.append(f"Failed to process backend {name}: {exc}")
                    self._logger.warning(f"Failed to process backend {name}: {exc}")

            elif name == 'staging':
                try:
                    # Validate and process staging
                    staging_data = paths_data['staging']
                    if not isinstance(staging_data, dict):
                        warnings.append("Staging must be a dictionary")
                        continue

                    staging_db = {
                        'staging_mounted': bool(staging_data.get('staging_mounted', False)),
                        'staging_mount_root': str(staging_data.get('staging_mount_root')) if staging_data.get('staging_mount_root') else None,
                        'size_gb': int(staging_data.get('size_gb', 50))
                    }

                    self.index_db.save_staging(staging_db)
                    success = True

                except Exception as exc:
                    warnings.append(f"Failed to process staging: {exc}")
                    self._logger.warning(f"Failed to process staging: {exc}")

        if backends_processed > 0:
            success = True

        return success, warnings

    def _process_limits(self, limits_data: dict) -> Tuple[bool, List[str]]:
        """Process limits configuration section.

        Args:
            limits_data: Limits configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []

        if not isinstance(limits_data, dict):
            warnings.append("Limits section must be a dictionary")
            return False, warnings

        try:
            limits_db = {
                'max_zip_total_gb': int(limits_data.get('max_zip_total_gb', 100)),
                'one_zip_cache_at_a_time': bool(limits_data.get('one_zip_cache_at_a_time', False))
            }

            self.index_db.save_limits(limits_db)
            return True, warnings

        except Exception as exc:
            warnings.append(f"Failed to process limits: {exc}")
            self._logger.warning(f"Failed to process limits: {exc}")
            return False, warnings

    def _process_indexing(self, indexing_data: dict) -> Tuple[bool, List[str]]:
        """Process indexing configuration section.

        Args:
            indexing_data: Indexing configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []

        if not isinstance(indexing_data, dict):
            warnings.append("Indexing section must be a dictionary")
            return False, warnings

        try:
            score_weights = indexing_data.get('score_weights', {})
            score_weights_json = json.dumps(score_weights) if score_weights else None

            indexing_db = {
                'min_full_reindex_days': int(indexing_data.get('min_full_reindex_days', 30)),
                'max_full_reindex_days': int(indexing_data.get('max_full_reindex_days', 90)),
                'hot_window_days': int(indexing_data.get('hot_window_days', 7)),
                'hot_radius': int(indexing_data.get('hot_radius', 10)),
                'daily_full_reindex_budget': int(indexing_data.get('daily_full_reindex_budget', 5)),
                'daily_cheap_check_budget': int(indexing_data.get('daily_cheap_check_budget', 10)),
                'max_full_reindex_per_14d': int(indexing_data.get('max_full_reindex_per_14d', 10)),
                'max_cheap_checks_per_day': int(indexing_data.get('max_cheap_checks_per_day', 50)),
                'allow_early_full_on_change': bool(indexing_data.get('allow_early_full_on_change', True)),
                'early_full_requires_hot': bool(indexing_data.get('early_full_requires_hot', True)),
                'score_weights': score_weights_json
            }

            self.index_db.save_indexing(indexing_db)
            return True, warnings

        except Exception as exc:
            warnings.append(f"Failed to process indexing: {exc}")
            self._logger.warning(f"Failed to process indexing: {exc}")
            return False, warnings

    def _process_auth(self, auth_data: dict) -> Tuple[bool, List[str]]:
        """Process auth configuration section.

        Args:
            auth_data: Auth configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []

        if not isinstance(auth_data, dict):
            warnings.append("Auth section must be a dictionary")
            return False, warnings

        try:
            oidc_config = auth_data.get('oidc', {})
            ldap_config = auth_data.get('ldap', {})
            proxy_config = auth_data.get('proxy_header', {})

            auth_db = {
                'oidc_config': json.dumps(oidc_config),
                'ldap_config': json.dumps(ldap_config),
                'proxy_config': json.dumps(proxy_config)
            }

            self.index_db.save_auth(auth_db)
            return True, warnings

        except Exception as exc:
            warnings.append(f"Failed to process auth: {exc}")
            self._logger.warning(f"Failed to process auth: {exc}")
            return False, warnings

    def _process_tls(self, tls_data: dict) -> Tuple[bool, List[str]]:
        """Process TLS configuration section.

        Args:
            tls_data: TLS configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []

        if not isinstance(tls_data, dict):
            warnings.append("TLS section must be a dictionary")
            return False, warnings

        try:
            manual_config = tls_data.get('manual', {})
            http_config = tls_data.get('http', {})
            dns01_config = tls_data.get('dns01', {})

            tls_db = {
                'enabled': bool(tls_data.get('enabled', False)),
                'mode': tls_data.get('mode', 'manual'),
                'manual_config': json.dumps(manual_config),
                'http_config': json.dumps(http_config),
                'dns01_config': json.dumps(dns01_config)
            }

            self.index_db.save_tls(tls_db)
            return True, warnings

        except Exception as exc:
            warnings.append(f"Failed to process TLS: {exc}")
            self._logger.warning(f"Failed to process TLS: {exc}")
            return False, warnings

    def _process_cookies(self, cookies_data: dict) -> Tuple[bool, List[str]]:
        """Process cookies configuration section.

        Args:
            cookies_data: Cookies configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []
        cookies_processed = 0

        if not isinstance(cookies_data, dict):
            warnings.append("Cookies section must be a dictionary")
            return False, warnings

        for domain, cookie_data in cookies_data.items():
            try:
                if not isinstance(cookie_data, dict):
                    warnings.append(f"Cookie {domain} must be a dictionary")
                    continue

                cookie_db = {
                    'domain': domain.lower(),
                    'cookie_content': cookie_data.get('cookie_jar', ''),
                    'credfile_path': str(cookie_data.get('credfile')) if cookie_data.get('credfile') else None
                }

                self.index_db.save_cookie(cookie_db)
                cookies_processed += 1

            except Exception as exc:
                warnings.append(f"Failed to process cookie {domain}: {exc}")
                self._logger.warning(f"Failed to process cookie {domain}: {exc}")

        return cookies_processed > 0, warnings

    def _process_webdav(self, webdav_data: dict) -> Tuple[bool, List[str]]:
        """Process webdav configuration section.

        Args:
            webdav_data: WebDAV configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []
        shares_processed = 0

        if not isinstance(webdav_data, dict):
            warnings.append("WebDAV section must be a dictionary")
            return False, warnings

        for name, share_data in webdav_data.items():
            try:
                if not isinstance(share_data, dict):
                    warnings.append(f"Share {name} must be a dictionary")
                    continue

                users_config = share_data.get('users', {})
                share_db = {
                    'name': name,
                    'backend_folder': str(share_data.get('backend_folder', '')),
                    'frontend_folder': str(share_data.get('frontend_folder', '')),
                    'writable': bool(share_data.get('writable', True)),
                    'cachelink_overlay': bool(share_data.get('cachelink_overlay', True)),
                    'users_config': json.dumps(users_config)
                }

                self.index_db.save_share(share_db)
                shares_processed += 1

            except Exception as exc:
                warnings.append(f"Failed to process share {name}: {exc}")
                self._logger.warning(f"Failed to process share {name}: {exc}")

        return shares_processed > 0, warnings

    def _process_users(self, users_data: dict) -> Tuple[bool, List[str]]:
        """Process users configuration section.

        Args:
            users_data: Users configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []
        users_processed = 0

        if not isinstance(users_data, dict):
            warnings.append("Users section must be a dictionary")
            return False, warnings

        for username, user_data in users_data.items():
            try:
                if not isinstance(user_data, dict):
                    warnings.append(f"User {username} must be a dictionary")
                    continue

                user_db = {
                    'username': username,
                    'password_plain': user_data.get('password_plain'),
                    'password_hash': user_data.get('password_hash'),
                    'enabled': bool(user_data.get('enabled', True)),
                    'is_admin': bool(user_data.get('is_admin', False)),
                    'purpose': user_data.get('purpose', 'webui')
                }

                self.index_db.save_user(user_db)
                users_processed += 1

            except Exception as exc:
                warnings.append(f"Failed to process user {username}: {exc}")
                self._logger.warning(f"Failed to process user {username}: {exc}")

        return users_processed > 0, warnings

    def _process_cachelinks(self, cachelinks_data: list) -> Tuple[bool, List[str]]:
        """Process cachelinks configuration section.

        Args:
            cachelinks_data: Cachelinks configuration data

        Returns:
            Tuple of (success: bool, warnings: list[str])
        """
        warnings = []
        cachelinks_processed = 0

        if not isinstance(cachelinks_data, list):
            warnings.append("Cachelinks section must be a list")
            return False, warnings

        cachelinks_db = []
        for cachelink_data in cachelinks_data:
            try:
                if not isinstance(cachelink_data, dict):
                    warnings.append("Cachelink entry must be a dictionary")
                    continue

                cachelink_db = {
                    'canonical_id': cachelink_data.get('canonical_id'),
                    'backend_path': cachelink_data.get('backend_path'),
                    'url': cachelink_data.get('url'),
                    'subfolder': cachelink_data.get('subfolder'),
                    'mode': cachelink_data.get('mode'),
                    'source_file': cachelink_data.get('source_file')
                }

                cachelinks_db.append(cachelink_db)
                cachelinks_processed += 1

            except Exception as exc:
                warnings.append(f"Failed to process cachelink: {exc}")
                self._logger.warning(f"Failed to process cachelink: {exc}")

        if cachelinks_processed > 0:
            self.index_db.save_cachelinks(cachelinks_db)

        return cachelinks_processed > 0, warnings
