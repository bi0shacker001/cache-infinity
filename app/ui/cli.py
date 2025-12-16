"""Command-line interface for CacheInfinity administration."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..core.service import CacheInfinityService
from ..core.config import load_settings
from ..auth.credentials import load_credentials
from ..ui.management import ManagementLayer

_logger = logging.getLogger(__name__)


class CLIInterface:
    """Command-line interface for CacheInfinity administration."""
    
    def __init__(self, service: CacheInfinityService):
        self.service = service
        self.management = ManagementLayer(service)
        self.cli_api_key = None
        
    def authenticate_cli(self, username: str, password: str) -> bool:
        """Authenticate CLI user using API key or credentials."""
        # Try API key authentication first
        if self.cli_api_key:
            result = self.management.authenticate_request("api-key", self.cli_api_key)
            if result.get('authenticated'):
                return True
        
        # Fall back to credentials
        result = self.management.authenticate_request(username, password)
        return result.get('authenticated', False)
    
    def run_command(self, args: argparse.Namespace) -> int:
        """Run a CLI command based on parsed arguments."""
        try:
            if args.command == 'status':
                return self.show_status()
            elif args.command == 'users':
                return self.manage_users(args)
            elif args.command == 'cachelinks':
                return self.manage_cachelinks(args)
            elif args.command == 'cookies':
                return self.manage_cookies(args)
            elif args.command == 'storage':
                return self.manage_storage(args)
            elif args.command == 'indexing':
                return self.manage_indexing(args)
            elif args.command == 'config':
                return self.manage_config(args)
            else:
                _logger.error(f"Unknown command: {args.command}")
                return 1
                
        except Exception as e:
            _logger.error(f"Command failed: {e}")
            return 1
    
    def show_status(self) -> int:
        """Show system status."""
        try:
            status = self.management.get_system_status()
            print(json.dumps(status, indent=2, default=str))
            return 0
        except Exception as e:
            _logger.error(f"Failed to get status: {e}")
            return 1
    
    def manage_users(self, args: argparse.Namespace) -> int:
        """Manage users."""
        if args.action == 'list':
            users = self.management.list_users(purpose="webui")
            print(json.dumps(users, indent=2))
            return 0
        elif args.action == 'create':
            self.management.upsert_user(
                username=args.username,
                password=args.password,
                enabled=True,
                is_admin=args.admin
            )
            print(f"User {args.username} created successfully")
            return 0
        elif args.action == 'update':
            self.management.upsert_user(
                username=args.username,
                password=args.password,
                enabled=args.enabled,
                is_admin=args.admin
            )
            print(f"User {args.username} updated successfully")
            return 0
        elif args.action == 'disable':
            self.management.disable_user(args.username)
            print(f"User {args.username} disabled successfully")
            return 0
        else:
            _logger.error(f"Unknown user action: {args.action}")
            return 1
    
    def manage_cachelinks(self, args: argparse.Namespace) -> int:
        """Manage cachelinks."""
        if args.action == 'list':
            cachelinks = self.management.describe_cachelinks()
            print(json.dumps(cachelinks, indent=2))
            return 0
        elif args.action == 'create':
            self.management.create_cachelink(
                parent_path=args.parent_path,
                name=args.name,
                url=args.url,
                subfolder=args.subfolder
            )
            print(f"Cachelink {args.name} created successfully")
            return 0
        elif args.action == 'update':
            self.management.update_cachelink(
                canonical_id=args.canonical_id,
                url=args.url,
                subfolder=args.subfolder
            )
            print(f"Cachelink {args.canonical_id} updated successfully")
            return 0
        elif args.action == 'delete':
            self.management.delete_cachelink(args.canonical_id)
            print(f"Cachelink {args.canonical_id} deleted successfully")
            return 0
        elif args.action == 'preview':
            preview = self.management.preview_cachelink(args.url, args.subfolder)
            print(json.dumps(preview, indent=2))
            return 0
        else:
            _logger.error(f"Unknown cachelink action: {args.action}")
            return 1
    
    def manage_cookies(self, args: argparse.Namespace) -> int:
        """Manage cookies."""
        if args.action == 'list':
            cookies = self.management.describe_cookies()
            print(json.dumps(cookies, indent=2))
            return 0
        elif args.action == 'upload':
            cookie_content = args.cookie_content
            if args.cookie_file:
                cookie_content = Path(args.cookie_file).read_text()
            self.management.upload_cookie_file(args.domain, cookie_content)
            print(f"Cookies uploaded for {args.domain}")
            return 0
        elif args.action == 'refresh':
            self.management.regenerate_cookie(args.domain)
            print(f"Cookies refreshed for {args.domain}")
            return 0
        elif args.action == 'update-credentials':
            self.management.update_cookie_credentials(
                args.domain,
                args.username,
                args.password
            )
            print(f"Credentials updated for {args.domain}")
            return 0
        else:
            _logger.error(f"Unknown cookie action: {args.action}")
            return 1
    
    def manage_storage(self, args: argparse.Namespace) -> int:
        """Manage storage."""
        if args.action == 'list':
            entries = self.management.list_storage_entries(
                location=args.location,
                relative_path=args.path,
                sort_by=args.sort_by,
                sort_order=args.sort_order,
                view_mode=args.view_mode,
                show_hidden=args.show_hidden,
                search_query=args.search_query
            )
            print(json.dumps(entries, indent=2))
            return 0
        elif args.action == 'upload':
            file_data = Path(args.file).read_bytes()
            self.management.upload_storage_file(
                location=args.location,
                relative_path=args.path,
                filename=Path(args.file).name,
                file_data=file_data
            )
            print(f"File {args.file} uploaded successfully")
            return 0
        elif args.action == 'create-folder':
            self.management.create_storage_folder(
                location=args.location,
                relative_path=args.path,
                folder_name=args.name
            )
            print(f"Folder {args.name} created successfully")
            return 0
        elif args.action == 'delete':
            self.management.delete_storage_entry(
                location=args.location,
                relative_path=args.path
            )
            print(f"Entry {args.path} deleted successfully")
            return 0
        else:
            _logger.error(f"Unknown storage action: {args.action}")
            return 1
    
    def manage_indexing(self, args: argparse.Namespace) -> int:
        """Manage indexing."""
        if args.action == 'status':
            status = self.management.get_all_index_status()
            print(json.dumps(status, indent=2))
            return 0
        elif args.action == 'trigger':
            self.management.trigger_reindex(args.canonical_id)
            print(f"Reindex triggered for {args.canonical_id}")
            return 0
        elif args.action == 'degraded':
            degraded = self.management.list_degraded_targets()
            print(json.dumps(degraded, indent=2))
            return 0
        else:
            _logger.error(f"Unknown indexing action: {args.action}")
            return 1
    
    def manage_config(self, args: argparse.Namespace) -> int:
        """Manage configuration."""
        if args.action == 'get':
            config = self.management.get_config_payload()
            print(json.dumps(config, indent=2))
            return 0
        elif args.action == 'update':
            settings_text = None
            cachelinks_text = None
            
            if args.settings_file:
                settings_text = Path(args.settings_file).read_text()
            if args.cachelinks_file:
                cachelinks_text = Path(args.cachelinks_file).read_text()
            
            self.management.update_config(
                settings_text=settings_text,
                cachelinks_text=cachelinks_text
            )
            print("Configuration updated successfully")
            return 0
        elif args.action == 'detail':
            detail = self.management.describe_settings_detail()
            print(json.dumps(detail, indent=2))
            return 0
        else:
            _logger.error(f"Unknown config action: {args.action}")
            return 1


def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        description="CacheInfinity Command-Line Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show system status
  cacheinfinity-cli status
  
  # List users
  cacheinfinity-cli users list
  
  # Create a user
  cacheinfinity-cli users create --username admin --password secret --admin
  
  # List cachelinks
  cacheinfinity-cli cachelinks list
  
  # Create a cachelink
  cacheinfinity-cli cachelinks create --parent-path games --name psx --url https://archive.org/download/psx_games
  
  # Upload cookies
  cacheinfinity-cli cookies upload --domain archive.org --cookie-file cookies.txt
  
  # List storage entries
  cacheinfinity-cli storage list --location backend --path /games
  
  # Show indexing status
  cacheinfinity-cli indexing status
        """
    )
    
    parser.add_argument(
        '--config-dir',
        type=Path,
        default=Path('/config'),
        help='Configuration directory (default: /config)'
    )
    
    parser.add_argument(
        '--username',
        type=str,
        help='Username for authentication'
    )
    
    parser.add_argument(
        '--password',
        type=str,
        help='Password for authentication (will prompt if not provided)'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='count',
        default=0,
        help='Increase verbosity (can be used multiple times)'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Show system status')
    
    # Users command
    users_parser = subparsers.add_parser('users', help='Manage users')
    users_subparsers = users_parser.add_subparsers(dest='action', help='User actions')
    
    users_list = users_subparsers.add_parser('list', help='List users')
    
    users_create = users_subparsers.add_parser('create', help='Create a user')
    users_create.add_argument('--username', required=True, help='Username')
    users_create.add_argument('--password', help='Password')
    users_create.add_argument('--admin', action='store_true', help='Make user admin')
    
    users_update = users_subparsers.add_parser('update', help='Update a user')
    users_update.add_argument('--username', required=True, help='Username')
    users_update.add_argument('--password', help='New password')
    users_update.add_argument('--enabled', action='store_true', help='Enable user')
    users_update.add_argument('--admin', action='store_true', help='Make user admin')
    
    users_disable = users_subparsers.add_parser('disable', help='Disable a user')
    users_disable.add_argument('--username', required=True, help='Username')
    
    # Cachelinks command
    cachelinks_parser = subparsers.add_parser('cachelinks', help='Manage cachelinks')
    cachelinks_subparsers = cachelinks_parser.add_subparsers(dest='action', help='Cachelink actions')
    
    cachelinks_list = cachelinks_subparsers.add_parser('list', help='List cachelinks')
    
    cachelinks_create = cachelinks_subparsers.add_parser('create', help='Create a cachelink')
    cachelinks_create.add_argument('--parent-path', required=True, help='Parent path')
    cachelinks_create.add_argument('--name', required=True, help='Cachelink name')
    cachelinks_create.add_argument('--url', required=True, help='Remote URL')
    cachelinks_create.add_argument('--subfolder', default='/', help='Subfolder (default: /)')
    
    cachelinks_update = cachelinks_subparsers.add_parser('update', help='Update a cachelink')
    cachelinks_update.add_argument('--canonical-id', required=True, help='Canonical ID')
    cachelinks_update.add_argument('--url', help='New URL')
    cachelinks_update.add_argument('--subfolder', help='New subfolder')
    
    cachelinks_delete = cachelinks_subparsers.add_parser('delete', help='Delete a cachelink')
    cachelinks_delete.add_argument('--canonical-id', required=True, help='Canonical ID')
    
    cachelinks_preview = cachelinks_subparsers.add_parser('preview', help='Preview a cachelink')
    cachelinks_preview.add_argument('--url', required=True, help='Remote URL')
    cachelinks_preview.add_argument('--subfolder', default='/', help='Subfolder (default: /)')
    
    # Cookies command
    cookies_parser = subparsers.add_parser('cookies', help='Manage cookies')
    cookies_subparsers = cookies_parser.add_subparsers(dest='action', help='Cookie actions')
    
    cookies_list = cookies_subparsers.add_parser('list', help='List cookies')
    
    cookies_upload = cookies_subparsers.add_parser('upload', help='Upload cookies')
    cookies_upload.add_argument('--domain', required=True, help='Domain')
    cookies_upload.add_argument('--cookie-content', help='Cookie content')
    cookies_upload.add_argument('--cookie-file', help='Cookie file path')
    
    cookies_refresh = cookies_subparsers.add_parser('refresh', help='Refresh cookies')
    cookies_refresh.add_argument('--domain', required=True, help='Domain')
    
    cookies_update_creds = cookies_subparsers.add_parser('update-credentials', help='Update cookie credentials')
    cookies_update_creds.add_argument('--domain', required=True, help='Domain')
    cookies_update_creds.add_argument('--username', required=True, help='Username')
    cookies_update_creds.add_argument('--password', required=True, help='Password')
    
    # Storage command
    storage_parser = subparsers.add_parser('storage', help='Manage storage')
    storage_subparsers = storage_parser.add_subparsers(dest='action', help='Storage actions')
    
    storage_list = storage_subparsers.add_parser('list', help='List storage entries')
    storage_list.add_argument('--location', choices=['backend', 'staging'], default='backend', help='Storage location')
    storage_list.add_argument('--path', default='/', help='Path to list (default: /)')
    storage_list.add_argument('--sort-by', help='Sort by field')
    storage_list.add_argument('--sort-order', choices=['asc', 'desc'], default='asc', help='Sort order')
    storage_list.add_argument('--view-mode', choices=['list', 'grid'], default='list', help='View mode')
    storage_list.add_argument('--show-hidden', action='store_true', help='Show hidden files')
    storage_list.add_argument('--search-query', help='Search query')
    
    storage_upload = storage_subparsers.add_parser('upload', help='Upload a file')
    storage_upload.add_argument('--location', choices=['backend', 'staging'], default='backend', help='Storage location')
    storage_upload.add_argument('--path', required=True, help='Destination path')
    storage_upload.add_argument('--file', required=True, help='File to upload')
    
    storage_create_folder = storage_subparsers.add_parser('create-folder', help='Create a folder')
    storage_create_folder.add_argument('--location', choices=['backend', 'staging'], default='backend', help='Storage location')
    storage_create_folder.add_argument('--path', required=True, help='Parent path')
    storage_create_folder.add_argument('--name', required=True, help='Folder name')
    
    storage_delete = storage_subparsers.add_parser('delete', help='Delete an entry')
    storage_delete.add_argument('--location', choices=['backend', 'staging'], default='backend', help='Storage location')
    storage_delete.add_argument('--path', required=True, help='Path to delete')
    
    # Indexing command
    indexing_parser = subparsers.add_parser('indexing', help='Manage indexing')
    indexing_subparsers = indexing_parser.add_subparsers(dest='action', help='Indexing actions')
    
    indexing_status = indexing_subparsers.add_parser('status', help='Show indexing status')
    
    indexing_trigger = indexing_subparsers.add_parser('trigger', help='Trigger reindexing')
    indexing_trigger.add_argument('--canonical-id', required=True, help='Canonical ID')
    
    indexing_degraded = indexing_subparsers.add_parser('degraded', help='List degraded targets')
    
    # Config command
    config_parser = subparsers.add_parser('config', help='Manage configuration')
    config_subparsers = config_parser.add_subparsers(dest='action', help='Config actions')
    
    config_get = config_subparsers.add_parser('get', help='Get configuration')
    
    config_update = config_subparsers.add_parser('update', help='Update configuration')
    config_update.add_argument('--settings-file', help='Settings file path')
    config_update.add_argument('--cachelinks-file', help='Cachelinks file path')
    
    config_detail = config_subparsers.add_parser('detail', help='Get detailed settings')
    
    return parser


def setup_logging(verbose: int) -> None:
    """Set up logging configuration."""
    if verbose >= 3:
        level = logging.DEBUG
    elif verbose == 2:
        level = logging.INFO
    elif verbose == 1:
        level = logging.WARNING
    else:
        level = logging.ERROR
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main():
    """Main CLI entry point."""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Set up logging
    setup_logging(args.verbose)
    
    try:
        # Load configuration
        settings = load_settings(args.config_dir)
        credentials_file = args.config_dir / "credentials" / "users.yaml"
        credentials = load_credentials(credentials_file) if credentials_file.exists() else None
        
        # Create service
        service = CacheInfinityService.from_settings(settings, credentials)
        
        # Create CLI interface
        cli = CLIInterface(service)
        
        # Get credentials if not provided
        username = args.username
        password = args.password
        
        if not username:
            username = input("Username: ")
        
        if not password:
            password = getpass.getpass("Password: ")
        
        # Authenticate
        if not cli.authenticate_cli(username, password):
            print("Authentication failed")
            return 1
        
        # Run command
        return cli.run_command(args)
        
    except KeyboardInterrupt:
        print("\nOperation cancelled")
        return 1
    except Exception as e:
        _logger.error(f"CLI error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())