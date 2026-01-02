#!/usr/bin/env python3
"""
CacheInfinity SPEC Compliance Validation Tool

This tool provides automated validation of CacheInfinity's implementation against
the specification requirements defined in SPEC.md. It helps identify compliance
gaps and track progress across different phases of implementation.
"""

import os
import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, asdict
import yaml


@dataclass
class ComplianceRequirement:
    """Represents a SPEC requirement to validate."""
    spec_section: str
    requirement_id: str
    description: str
    implementation_path: str
    status: str  # pending, implemented, partial, not_implemented
    priority: str  # high, medium, low
    notes: str = ""


@dataclass
class ComplianceReport:
    """Compliance validation report."""
    total_requirements: int
    implemented: int
    partial: int
    not_implemented: int
    pending: int
    compliance_percentage: float
    requirements: List[ComplianceRequirement]
    critical_gaps: List[ComplianceRequirement]


class SPECComplianceValidator:
    """Validates CacheInfinity implementation against SPEC.md requirements."""
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.spec_path = self.project_root / "SPEC.md"
        self.requirements = self._load_requirements()
        
    def _load_requirements(self) -> List[ComplianceRequirement]:
        """Load SPEC requirements from predefined mapping."""
        return [
            # Phase 1: Critical Compliance (High Priority)
            ComplianceRequirement(
                spec_section="2.1",
                requirement_id="TWO_PORT_ARCHITECTURE",
                description="Two-port architecture with hosting and admin ports",
                implementation_path="app/hosting/dispatcher.py",
                status="implemented",
                priority="high"
            ),
            ComplianceRequirement(
                spec_section="4.1",
                requirement_id="VFS_ARCHITECTURE",
                description="Virtual Filesystem Layer providing unified interface",
                implementation_path="app/storage/vfs.py",
                status="implemented",
                priority="high"
            ),
            ComplianceRequirement(
                spec_section="5.1",
                requirement_id="SHARE_SCHEMA",
                description="Share schema with datadir_folder, frontend_folder, users",
                implementation_path="app/db/schema.py",
                status="implemented",
                priority="high"
            ),
            ComplianceRequirement(
                spec_section="16.1",
                requirement_id="ADMIN_WEBUI",
                description="Admin WebUI for configuration and maintenance",
                implementation_path="app/ui/web/",
                status="implemented",
                priority="medium"
            ),
            ComplianceRequirement(
                spec_section="16.2",
                requirement_id="ADMIN_API",
                description="Read-only admin API for status and statistics",
                implementation_path="app/ui/api.py",
                status="implemented",
                priority="medium"
            ),
            
            # Phase 2: Core Feature Completion
            ComplianceRequirement(
                spec_section="6.1",
                requirement_id="FTP_FTPS_IMPLEMENTATION",
                description="FTP/FTPS implementation using pyftpdlib",
                implementation_path="app/hosting/ftp.py",
                status="implemented",
                priority="medium"
            ),
            ComplianceRequirement(
                spec_section="6.2",
                requirement_id="SFTP_IMPLEMENTATION",
                description="SFTP implementation using AsyncSSH",
                implementation_path="app/hosting/sftp.py",
                status="partial",
                priority="high",
                notes="Protocol handler exists but missing virtual authorized_keys"
            ),
            ComplianceRequirement(
                spec_section="6.3",
                requirement_id="VIRTUAL_AUTHORIZED_KEYS",
                description="Virtual .ssh/authorized_keys management via SFTP",
                implementation_path="app/hosting/sftp.py",
                status="not_implemented",
                priority="high",
                notes="Critical gap - missing virtual authorized_keys functionality"
            ),
            ComplianceRequirement(
                spec_section="12.1",
                requirement_id="ZIP_CACHING_SIZE_LIMITS",
                description="Zip caching size limits validation",
                implementation_path="app/storage/staging.py",
                status="partial",
                priority="medium",
                notes="Basic zip support exists but size limits not implemented"
            ),
            ComplianceRequirement(
                spec_section="12.2",
                requirement_id="ONE_ZIP_AT_A_TIME",
                description="One-zip-at-a-time locking mechanism",
                implementation_path="app/storage/staging.py",
                status="not_implemented",
                priority="medium"
            ),
            
            # Phase 3: Testing and Robustness
            ComplianceRequirement(
                spec_section="11.4",
                requirement_id="PYCURL_DOWNLOADER",
                description="PycURL-based downloader for HTTP(S) and FTP transfers",
                implementation_path="app/net/fetcher.py",
                status="implemented",
                priority="medium"
            ),
            ComplianceRequirement(
                spec_section="10.1",
                requirement_id="INDEXING_POLICY",
                description="Indexing policy with daily recache and budgeting",
                implementation_path="app/net/indexer.py",
                status="implemented",
                priority="medium"
            ),
            ComplianceRequirement(
                spec_section="17",
                requirement_id="ERROR_HANDLING",
                description="Comprehensive error handling and observability",
                implementation_path="app/core/logging.py",
                status="implemented",
                priority="medium"
            ),
        ]
    
    def validate_implementation(self) -> ComplianceReport:
        """Validate implementation against SPEC requirements."""
        validated_requirements = []
        critical_gaps = []
        
        for req in self.requirements:
            # Check if implementation file exists
            if req.status == "not_implemented":
                # For not implemented, check if any related files exist
                if self._check_related_files(req):
                    req.status = "partial"
                    req.notes = "Related files found but core implementation missing"
            
            # Update status based on file existence and content analysis
            req = self._analyze_implementation(req)
            
            validated_requirements.append(req)
            
            # Track critical gaps
            if req.status == "not_implemented" and req.priority == "high":
                critical_gaps.append(req)
        
        # Calculate statistics
        total = len(validated_requirements)
        implemented = len([r for r in validated_requirements if r.status == "implemented"])
        partial = len([r for r in validated_requirements if r.status == "partial"])
        not_implemented = len([r for r in validated_requirements if r.status == "not_implemented"])
        pending = len([r for r in validated_requirements if r.status == "pending"])
        
        compliance_percentage = (implemented / total * 100) if total > 0 else 0
        
        return ComplianceReport(
            total_requirements=total,
            implemented=implemented,
            partial=partial,
            not_implemented=not_implemented,
            pending=pending,
            compliance_percentage=compliance_percentage,
            requirements=validated_requirements,
            critical_gaps=critical_gaps
        )
    
    def _check_related_files(self, requirement: ComplianceRequirement) -> bool:
        """Check if any related files exist for a requirement."""
        path = self.project_root / requirement.implementation_path
        if path.exists():
            return True
        
        # Check for files with similar names
        parent = path.parent
        if parent.exists():
            pattern = path.name.replace(".py", "") + "*.py"
            return any(parent.glob(pattern))
        
        return False
    
    def _analyze_implementation(self, requirement: ComplianceRequirement) -> ComplianceRequirement:
        """Analyze implementation file for compliance indicators."""
        path = self.project_root / requirement.implementation_path
        
        if not path.exists():
            return requirement
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Look for compliance indicators in the code
            indicators = self._get_compliance_indicators(requirement.requirement_id)
            
            found_indicators = []
            for indicator in indicators:
                if re.search(indicator, content, re.IGNORECASE):
                    found_indicators.append(indicator)
            
            # Update status based on found indicators
            if len(found_indicators) >= len(indicators) * 0.8:
                requirement.status = "implemented"
            elif len(found_indicators) > 0:
                requirement.status = "partial"
                requirement.notes = f"Found {len(found_indicators)}/{len(indicators)} compliance indicators"
            
        except Exception as e:
            requirement.notes = f"Error analyzing file: {str(e)}"
        
        return requirement
    
    def _get_compliance_indicators(self, requirement_id: str) -> List[str]:
        """Get compliance indicators for a specific requirement."""
        indicators = {
            "TWO_PORT_ARCHITECTURE": [
                r"DispatcherMiddleware",
                r"hosting.*port",
                r"admin.*port",
                r"/dav",
                r"/api"
            ],
            "VFS_ARCHITECTURE": [
                r"VirtualFileSystem",
                r"unified.*interface",
                r"cachelink.*integration",
                r"path.*resolution"
            ],
            "FTP_FTPS_IMPLEMENTATION": [
                r"pyftpdlib",
                r"FTP.*handler",
                r"FTPS.*handler",
                r"permission.*mapping"
            ],
            "SFTP_IMPLEMENTATION": [
                r"AsyncSSH",
                r"SFTP.*handler",
                r"SSH.*protocol",
                r"virtual.*authorized_keys"
            ],
            "VIRTUAL_AUTHORIZED_KEYS": [
                r"authorized_keys",
                r"virtual.*ssh",
                r"public.*key.*auth",
                r"ssh.*host.*keys"
            ],
            "ZIP_CACHING_SIZE_LIMITS": [
                r"max_zip.*size",
                r"size.*limit",
                r"zip.*caching",
                r"staging.*volume"
            ],
            "PYCURL_DOWNLOADER": [
                r"PycURL",
                r"curl.*download",
                r"HTTP.*transfer",
                r"FTP.*transfer"
            ],
            "INDEXING_POLICY": [
                r"indexer",
                r"remote.*listing",
                r"daily.*recache",
                r"budget.*limit"
            ]
        }
        
        return indicators.get(requirement_id, [])
    
    def generate_report(self, output_format: str = "text") -> str:
        """Generate compliance validation report."""
        report = self.validate_implementation()
        
        if output_format == "json":
            return self._format_json_report(report)
        elif output_format == "yaml":
            return self._format_yaml_report(report)
        else:
            return self._format_text_report(report)
    
    def _format_text_report(self, report: ComplianceReport) -> str:
        """Format report as text."""
        lines = [
            "=" * 60,
            "CacheInfinity SPEC Compliance Validation Report",
            "=" * 60,
            "",
            f"Overall Statistics:",
            f"  Total Requirements: {report.total_requirements}",
            f"  Implemented: {report.implemented} ({report.implemented/report.total_requirements*100:.1f}%)",
            f"  Partial: {report.partial} ({report.partial/report.total_requirements*100:.1f}%)",
            f"  Not Implemented: {report.not_implemented} ({report.not_implemented/report.total_requirements*100:.1f}%)",
            f"  Pending: {report.pending} ({report.pending/report.total_requirements*100:.1f}%)",
            f"  Overall Compliance: {report.compliance_percentage:.1f}%",
            "",
            "Critical Gaps (High Priority, Not Implemented):",
        ]
        
        if report.critical_gaps:
            for gap in report.critical_gaps:
                lines.append(f"  - {gap.requirement_id}: {gap.description}")
                if gap.notes:
                    lines.append(f"    Notes: {gap.notes}")
        else:
            lines.append("  None - All critical requirements are implemented!")
        
        lines.extend([
            "",
            "Detailed Requirements Status:",
            "-" * 40,
        ])
        
        for req in report.requirements:
            status_symbol = {
                "implemented": "✅",
                "partial": "🟡", 
                "not_implemented": "❌",
                "pending": "⏳"
            }.get(req.status, "?")
            
            lines.append(f"{status_symbol} {req.requirement_id} ({req.priority}): {req.description}")
            if req.notes:
                lines.append(f"    Notes: {req.notes}")
        
        return "\n".join(lines)
    
    def _format_json_report(self, report: ComplianceReport) -> str:
        """Format report as JSON."""
        return json.dumps(asdict(report), indent=2, default=str)
    
    def _format_yaml_report(self, report: ComplianceReport) -> str:
        """Format report as YAML."""
        return yaml.dump(asdict(report), default_flow_style=False, allow_unicode=True)


def main():
    """Main entry point for the compliance validator."""
    parser = argparse.ArgumentParser(description="Validate CacheInfinity SPEC compliance")
    parser.add_argument("--format", choices=["text", "json", "yaml"], default="text",
                       help="Output format for the report")
    parser.add_argument("--output", "-o", help="Output file (optional)")
    parser.add_argument("--project-root", default=".", help="Project root directory")
    
    args = parser.parse_args()
    
    # Create validator
    validator = SPECComplianceValidator(args.project_root)
    
    # Generate report
    report = validator.generate_report(args.format)
    
    # Output report
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"Report saved to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()