"""TLS certificate management for CacheInfinity."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.config import ConfigError, TLSSettings, TLSHTTPSettings, TLSDNS01Settings, TwoFileSettings

_LOGGER = logging.getLogger(__name__)


@dataclass
class TLSCertificate:
    """Represents a TLS certificate."""
    
    cert_path: Path
    key_path: Path
    domains: list[str]
    expires_at: Optional[str] = None
    issuer: Optional[str] = None


class TLSAutomationError(Exception):
    """Raised when TLS automation fails."""
    
    # TODO: Add specific error codes and detailed error messages
    pass


class TLSAutomationService:
    """Handles TLS certificate management using Certbot."""
    
    def __init__(self, config_dir: Path, tls_settings: TLSSettings):
        self.config_dir = config_dir
        self.tls_settings = tls_settings
        self.work_dir = config_dir / "tls"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.certs_dir = self.work_dir / "certs"
        self.certs_dir.mkdir(parents=True, exist_ok=True)
        self.live_dir = self.work_dir / "live"
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self.webroot_dir = self.work_dir / "webroot"
        self.webroot_dir.mkdir(parents=True, exist_ok=True)
        
    def ensure_certbot_installed(self) -> bool:
        """Check if certbot is installed and available."""
        try:
            result = subprocess.run(
                ["certbot", "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10
            )
            _LOGGER.debug("Certbot available: %s", result.stdout.strip())
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            _LOGGER.warning("Certbot not found. Install certbot to enable automated TLS.")
            return False
    
    def get_certificate(self) -> Optional[TLSCertificate]:
        """Obtain or renew a certificate based on TLS configuration."""
        if not self.ensure_certbot_installed():
            return None
            
        if self.tls_settings.mode == "http":
            return self._get_http_certificate()
        elif self.tls_settings.mode == "dns-01":
            return self._get_dns_certificate()
        else:
            raise ConfigError(f"Unsupported TLS automation mode: {self.tls_settings.mode}")
    
    def _get_http_certificate(self) -> Optional[TLSCertificate]:
        """Obtain certificate using HTTP-01 challenge."""
        http_settings = self.tls_settings.http
        domains = http_settings.domains
        
        if not domains:
            raise ConfigError("HTTP-01 mode requires domains to be specified")
        
        # Check if certificate already exists and is valid
        existing_cert = self._get_existing_certificate(domains)
        if existing_cert and self._is_certificate_valid(existing_cert):
            _LOGGER.debug("Using existing valid certificate for domains: %s", ", ".join(domains))
            return existing_cert
        
        _LOGGER.debug("Obtaining new certificate for domains: %s", ", ".join(domains))
        
        # Prepare certbot command
        cmd = [
            "certbot", "certonly",
            "--non-interactive",
            "--agree-tos",
            "--email", http_settings.email or "admin@example.com",
            "--webroot",
            "--webroot-path", str(self.webroot_dir),
            "--cert-name", self._get_cert_name(domains),
        ]
        
        if http_settings.staging:
            cmd.append("--staging")
            _LOGGER.debug("Using Let's Encrypt staging environment")
        
        # Add domains
        for domain in domains:
            cmd.extend(["-d", domain])
        
        # Run certbot
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=300  # 5 minutes timeout
            )
            _LOGGER.debug("Certificate obtained successfully: %s", result.stdout)
            
            # Return the new certificate
            return self._get_existing_certificate(domains)
            
        except subprocess.CalledProcessError as e:
            _LOGGER.error("Failed to obtain certificate: %s", e.stderr)
            raise TLSAutomationError(f"Certificate request failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            _LOGGER.error("Certificate request timed out")
            raise TLSAutomationError("Certificate request timed out")
    
    def _get_dns_certificate(self) -> Optional[TLSCertificate]:
        """Obtain certificate using DNS-01 challenge."""
        dns_settings = self.tls_settings.dns01
        domains = dns_settings.domains
        
        if not domains:
            raise ConfigError("DNS-01 mode requires domains to be specified")
        
        if not dns_settings.provider:
            raise ConfigError("DNS-01 mode requires a provider to be specified")
        
        # Check if certificate already exists and is valid
        existing_cert = self._get_existing_certificate(domains)
        if existing_cert and self._is_certificate_valid(existing_cert):
            _LOGGER.debug("Using existing valid certificate for domains: %s", ", ".join(domains))
            return existing_cert
        
        _LOGGER.debug("Obtaining new certificate for domains: %s", ", ".join(domains))
        
        # Prepare certbot command with DNS plugin
        cmd = [
            "certbot", "certonly",
            "--non-interactive",
            "--agree-tos",
            "--email", dns_settings.email or "admin@example.com",
        ]
        
        # Add DNS provider plugin
        provider = dns_settings.provider.lower()
        if provider.startswith("dns-"):
            plugin_name = provider
        else:
            plugin_name = f"dns-{provider}"
        
        cmd.extend(["--dns", plugin_name])
        
        if dns_settings.staging:
            cmd.append("--staging")
            _LOGGER.debug("Using Let's Encrypt staging environment")
        
        # Handle credentials file
        if dns_settings.credentials_ini:
            if not dns_settings.credentials_ini.exists():
                raise ConfigError(f"DNS credentials file not found: {dns_settings.credentials_ini}")
            cmd.extend(["--dns-credentials", str(dns_settings.credentials_ini)])
        
        # Add propagation delay
        if dns_settings.propagation_seconds:
            cmd.extend(["--dns-propagation-seconds", str(dns_settings.propagation_seconds)])
        
        # Add domains
        for domain in domains:
            cmd.extend(["-d", domain])
        
        # Run certbot
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=600  # 10 minutes timeout for DNS
            )
            _LOGGER.debug("Certificate obtained successfully: %s", result.stdout)
            
            # Return the new certificate
            return self._get_existing_certificate(domains)
            
        except subprocess.CalledProcessError as e:
            _LOGGER.error("Failed to obtain certificate: %s", e.stderr)
            raise TLSAutomationError(f"Certificate request failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            _LOGGER.error("Certificate request timed out")
            raise TLSAutomationError("Certificate request timed out")
    
    def renew_certificate(self) -> bool:
        """Attempt to renew the certificate if needed."""
        if not self.ensure_certbot_installed():
            return False
        
        try:
            # Check if renewal is needed
            result = subprocess.run(
                ["certbot", "renew", "--dry-run", "--non-interactive"],
                capture_output=True,
                text=True,
                check=True,
                timeout=120
            )
            _LOGGER.debug("Renewal dry-run successful: %s", result.stdout)
            
            # Actually renew
            result = subprocess.run(
                ["certbot", "renew", "--non-interactive"],
                capture_output=True,
                text=True,
                check=True,
                timeout=300
            )
            _LOGGER.debug("Certificate renewal successful: %s", result.stdout)
            return True
            
        except subprocess.CalledProcessError as e:
            _LOGGER.warning("Certificate renewal failed or not needed: %s", e.stderr)
            return False
        except subprocess.TimeoutExpired:
            _LOGGER.error("Certificate renewal timed out")
            return False
    
    def _get_existing_certificate(self, domains: list[str]) -> Optional[TLSCertificate]:
        """Get existing certificate for the given domains."""
        cert_name = self._get_cert_name(domains)
        cert_dir = self.live_dir / cert_name
        
        if not cert_dir.exists():
            return None
        
        cert_path = cert_dir / "fullchain.pem"
        key_path = cert_dir / "privkey.pem"
        
        if not cert_path.exists() or not key_path.exists():
            return None
        
        # Get certificate info
        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", str(cert_path), "-text", "-noout"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10
            )
            
            # Parse certificate information
            domains_from_cert = self._parse_certificate_domains(result.stdout)
            expires_at = self._parse_certificate_expiry(result.stdout)
            issuer = self._parse_certificate_issuer(result.stdout)
            
            return TLSCertificate(
                cert_path=cert_path,
                key_path=key_path,
                domains=domains_from_cert,
                expires_at=expires_at,
                issuer=issuer
            )
            
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            _LOGGER.warning("Failed to parse existing certificate")
            return None
    
    def _is_certificate_valid(self, cert: TLSCertificate) -> bool:
        """Check if certificate is still valid."""
        if not cert.expires_at:
            return False
        
        try:
            # Parse expiry date
            expiry_time = time.strptime(cert.expires_at, "%b %d %H:%M:%S %Y %Z")
            expiry_timestamp = time.mktime(expiry_time)
            current_timestamp = time.time()
            
            # Check if expires within 30 days
            days_until_expiry = (expiry_timestamp - current_timestamp) / (24 * 3600)
            
            return days_until_expiry > 30
            
        except (ValueError, TypeError):
            _LOGGER.warning("Could not parse certificate expiry date")
            return False
    
    def _get_cert_name(self, domains: list[str]) -> str:
        """Generate certificate name from domains."""
        # Use first domain as certificate name
        if not domains:
            return "cacheinfinity"
        return domains[0].replace(".", "_")
    
    def _parse_certificate_domains(self, cert_text: str) -> list[str]:
        """Parse certificate text to extract domain names."""
        domains = []
        
        # Extract Subject Alternative Names
        if "X509v3 Subject Alternative Name:" in cert_text:
            san_start = cert_text.find("X509v3 Subject Alternative Name:")
            san_section = cert_text[san_start:]
            san_line = san_section.split("\n")[1].strip()
            
            # Parse DNS entries
            for part in san_line.split(","):
                part = part.strip()
                if part.startswith("DNS:"):
                    domain = part[4:]
                    domains.append(domain)
        
        # Extract Common Name if no SAN entries
        if not domains and "Subject: CN=" in cert_text:
            cn_start = cert_text.find("Subject: CN=")
            cn_line = cert_text[cn_start:cert_text.find("\n", cn_start)]
            if "CN=" in cn_line:
                cn = cn_line.split("CN=")[1].split("/")[0].strip()
                domains.append(cn)
        
        return domains
    
    def _parse_certificate_expiry(self, cert_text: str) -> Optional[str]:
        """Parse certificate text to extract expiry date."""
        if "Not After :" in cert_text:
            expiry_line = cert_text.split("Not After :")[1].split("\n")[0].strip()
            return expiry_line
        return None
    
    def _parse_certificate_issuer(self, cert_text: str) -> Optional[str]:
        """Parse certificate text to extract issuer."""
        if "Issuer: " in cert_text:
            issuer_line = cert_text.split("Issuer: ")[1].split("\n")[0].strip()
            return issuer_line
        return None
    
    def cleanup_old_certificates(self, keep_days: int = 90) -> None:
        """Clean up old certificate files."""
        try:
            # Remove old certificate directories
            current_time = time.time()
            for cert_dir in self.live_dir.iterdir():
                if cert_dir.is_dir():
                    age_days = (current_time - cert_dir.stat().st_mtime) / (24 * 3600)
                    if age_days > keep_days:
                        _LOGGER.debug("Removing old certificate directory: %s", cert_dir)
                        shutil.rmtree(cert_dir)
        except Exception as e:
            _LOGGER.warning("Failed to cleanup old certificates: %s", e)


class TLSService:
    """Basic TLS service for certificate management."""
    
    def __init__(self, config_dir: Path, tls_settings: TLSSettings):
        self.config_dir = config_dir
        self.tls_settings = tls_settings
        # TODO: Implement basic TLS service functionality
    
    def get_certificate_path(self) -> Optional[Path]:
        """Get the path to the certificate file."""
        # TODO: Implement certificate path retrieval
        return None
    
    def get_key_path(self) -> Optional[Path]:
        """Get the path to the private key file."""
        # TODO: Implement key path retrieval
        return None


def create_tls_service(config_dir: Path, settings: TwoFileSettings) -> Optional[TLSAutomationService]:
    """Create TLS service using consolidated settings."""
    if settings.tls.enabled and settings.tls.mode in ("http", "dns-01"):
        return TLSAutomationService(config_dir, settings.tls)
    return None

def create_tls_automation_service(config_dir: Path, tls_settings: TLSSettings) -> Optional[TLSAutomationService]:
    """Create TLS automation service for certificate management."""
    if tls_settings.enabled and tls_settings.mode in ("http", "dns-01"):
        return TLSAutomationService(config_dir, tls_settings)
    return None