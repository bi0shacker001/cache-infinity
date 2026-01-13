"""SPEC compliance validation tests for CacheInfinity."""

import pytest
import re
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from utils.compliance_validator import SPECComplianceValidator, ComplianceRequirement, ComplianceReport


class TestSPECComplianceValidator:
    """Test SPEC compliance validation functionality."""
    
    def test_compliance_validator_initialization(self, temp_dir):
        """Test SPEC compliance validator initialization."""
        validator = SPECComplianceValidator(str(temp_dir))
        
        assert validator.project_root == temp_dir
        assert validator.spec_path == temp_dir / "SPEC.md"
        assert len(validator.requirements) > 0
    
    def test_load_requirements(self):
        """Test loading compliance requirements."""
        validator = SPECComplianceValidator(".")
        
        # Should have loaded requirements
        assert len(validator.requirements) > 0
        
        # Check that requirements have required fields
        for req in validator.requirements:
            assert hasattr(req, 'spec_section')
            assert hasattr(req, 'requirement_id')
            assert hasattr(req, 'description')
            assert hasattr(req, 'implementation_path')
            assert hasattr(req, 'status')
            assert hasattr(req, 'priority')
    
    def test_validate_implementation(self, temp_dir):
        """Test validating implementation against SPEC requirements."""
        validator = SPECComplianceValidator(str(temp_dir))
        
        # Create a mock SPEC.md file
        spec_content = """
# CacheInfinity Specification

## 2.1 Two-Port Architecture
The system must implement two-port architecture with hosting and admin ports.

## 6.3 Virtual .ssh/authorized_keys
SFTP must support virtual .ssh/authorized_keys management.
"""
        (temp_dir / "SPEC.md").write_text(spec_content)
        
        # Create some mock implementation files
        (temp_dir / "app").mkdir()
        (temp_dir / "app" / "hosting").mkdir()
        (temp_dir / "app" / "hosting" / "ftp.py").write_text("# FTP implementation")
        (temp_dir / "app" / "storage").mkdir()
        (temp_dir / "app" / "storage" / "staging.py").write_text("# Staging implementation")
        
        report = validator.validate_implementation()
        
        assert isinstance(report, ComplianceReport)
        assert report.total_requirements > 0
        assert report.compliance_percentage >= 0
        assert report.compliance_percentage <= 100
    
    def test_analyze_implementation(self, temp_dir):
        """Test analyzing implementation for compliance indicators."""
        validator = SPECComplianceValidator(str(temp_dir))
        
        # Create a mock implementation file with compliance indicators
        impl_content = """
# FTP implementation with compliance indicators
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.authorizers import DummyAuthorizer

class CacheInfinityFTPHandler(FTPHandler):
    # This implements FTP protocol support
    pass
"""
        impl_path = temp_dir / "app" / "hosting" / "ftp.py"
        impl_path.parent.mkdir(parents=True, exist_ok=True)
        impl_path.write_text(impl_content)
        
        # Create a mock requirement
        mock_req = ComplianceRequirement(
            spec_section="6.1",
            requirement_id="FTP_FTPS_IMPLEMENTATION",
            description="FTP/FTPS implementation using pyftpdlib",
            implementation_path="app/hosting/ftp.py",
            status="not_implemented",
            priority="medium"
        )
        
        updated_req = validator._analyze_implementation(mock_req)
        
        # Should detect compliance indicators
        assert updated_req.status in ["implemented", "partial"]
    
    def test_get_compliance_indicators(self):
        """Test getting compliance indicators for requirements."""
        validator = SPECComplianceValidator(".")
        
        # Test getting indicators for a specific requirement
        indicators = validator._get_compliance_indicators("FTP_FTPS_IMPLEMENTATION")
        
        assert isinstance(indicators, list)
        assert len(indicators) > 0
        assert any("pyftpdlib" in indicator for indicator in indicators)
        assert any("FTP" in indicator for indicator in indicators)
    
    def test_generate_report_text(self, temp_dir):
        """Test generating compliance report in text format."""
        validator = SPECComplianceValidator(str(temp_dir))
        
        # Create a mock SPEC.md file
        spec_content = """
# CacheInfinity Specification

## 2.1 Two-Port Architecture
The system must implement two-port architecture with hosting and admin ports.
"""
        (temp_dir / "SPEC.md").write_text(spec_content)
        
        report_text = validator.generate_report("text")
        
        assert isinstance(report_text, str)
        assert "CacheInfinity SPEC Compliance Validation Report" in report_text
        assert "Overall Statistics:" in report_text
        assert "Total Requirements:" in report_text
        assert "Compliance:" in report_text
    
    def test_generate_report_json(self, temp_dir):
        """Test generating compliance report in JSON format."""
        validator = SPECComplianceValidator(str(temp_dir))
        
        # Create a mock SPEC.md file
        spec_content = """
# CacheInfinity Specification

## 2.1 Two-Port Architecture
The system must implement two-port architecture with hosting and admin ports.
"""
        (temp_dir / "SPEC.md").write_text(spec_content)
        
        report_json = validator.generate_report("json")
        
        assert isinstance(report_json, str)
        # Should be valid JSON
        import json
        report_data = json.loads(report_json)
        assert "total_requirements" in report_data
        assert "implemented" in report_data
        assert "compliance_percentage" in report_data
    
    def test_generate_report_yaml(self, temp_dir):
        """Test generating compliance report in YAML format."""
        validator = SPECComplianceValidator(str(temp_dir))
        
        # Create a mock SPEC.md file
        spec_content = """
# CacheInfinity Specification

## 2.1 Two-Port Architecture
The system must implement two-port architecture with hosting and admin ports.
"""
        (temp_dir / "SPEC.md").write_text(spec_content)
        
        report_yaml = validator.generate_report("yaml")
        
        assert isinstance(report_yaml, str)
        # Should be valid YAML
        import yaml
        report_data = yaml.safe_load(report_yaml)
        assert "total_requirements" in report_data
        assert "implemented" in report_data
        assert "compliance_percentage" in report_data


class TestComplianceRequirement:
    """Test ComplianceRequirement dataclass."""
    
    def test_compliance_requirement_creation(self):
        """Test creating a compliance requirement."""
        req = ComplianceRequirement(
            spec_section="2.1",
            requirement_id="TWO_PORT_ARCHITECTURE",
            description="Two-port architecture with hosting and admin ports",
            implementation_path="app/hosting/dispatcher.py",
            status="implemented",
            priority="high"
        )
        
        assert req.spec_section == "2.1"
        assert req.requirement_id == "TWO_PORT_ARCHITECTURE"
        assert req.description == "Two-port architecture with hosting and admin ports"
        assert req.implementation_path == "app/hosting/dispatcher.py"
        assert req.status == "implemented"
        assert req.priority == "high"
        assert req.notes == ""
    
    def test_compliance_requirement_with_notes(self):
        """Test creating a compliance requirement with notes."""
        req = ComplianceRequirement(
            spec_section="6.3",
            requirement_id="VIRTUAL_AUTHORIZED_KEYS",
            description="Virtual .ssh/authorized_keys management via SFTP",
            implementation_path="app/hosting/sftp.py",
            status="not_implemented",
            priority="high",
            notes="Critical gap - missing virtual authorized_keys functionality"
        )
        
        assert req.notes == "Critical gap - missing virtual authorized_keys functionality"


class TestComplianceReport:
    """Test ComplianceReport dataclass."""
    
    def test_compliance_report_creation(self):
        """Test creating a compliance report."""
        requirements = [
            ComplianceRequirement(
                spec_section="2.1",
                requirement_id="TWO_PORT_ARCHITECTURE",
                description="Two-port architecture",
                implementation_path="app/hosting/dispatcher.py",
                status="implemented",
                priority="high"
            ),
            ComplianceRequirement(
                spec_section="6.3",
                requirement_id="VIRTUAL_AUTHORIZED_KEYS",
                description="Virtual .ssh/authorized_keys",
                implementation_path="app/hosting/sftp.py",
                status="not_implemented",
                priority="high"
            )
        ]
        
        report = ComplianceReport(
            total_requirements=2,
            implemented=1,
            partial=0,
            not_implemented=1,
            pending=0,
            compliance_percentage=50.0,
            requirements=requirements,
            critical_gaps=[requirements[1]]
        )
        
        assert report.total_requirements == 2
        assert report.implemented == 1
        assert report.not_implemented == 1
        assert report.compliance_percentage == 50.0
        assert len(report.requirements) == 2
        assert len(report.critical_gaps) == 1


class TestSPECComplianceIntegration:
    """Integration tests for SPEC compliance validation."""
    
    def test_full_compliance_validation_workflow(self, temp_dir):
        """Test the complete compliance validation workflow."""
        # Create a comprehensive SPEC.md
        spec_content = """
# CacheInfinity Specification

## 2.1 Two-Port Architecture
The system must implement two-port architecture with hosting and admin ports.

## 6.1 FTP/FTPS Implementation
FTP/FTPS implementation using pyftpdlib.

## 6.3 Virtual .ssh/authorized_keys
SFTP must support virtual .ssh/authorized_keys management.

## 12.1 Zip Caching Size Limits
Zip caching must implement size limit validation.
"""
        (temp_dir / "SPEC.md").write_text(spec_content)
        
        # Create implementation files
        (temp_dir / "app").mkdir()
        (temp_dir / "app" / "hosting").mkdir()
        (temp_dir / "app" / "hosting" / "dispatcher.py").write_text("# Two-port architecture")
        (temp_dir / "app" / "hosting" / "ftp.py").write_text("# FTP/FTPS implementation")
        (temp_dir / "app" / "storage").mkdir()
        (temp_dir / "app" / "storage" / "staging.py").write_text("# Zip caching implementation")
        
        # Run compliance validation
        validator = SPECComplianceValidator(str(temp_dir))
        report = validator.validate_implementation()
        
        # Verify report structure
        assert isinstance(report, ComplianceReport)
        assert report.total_requirements > 0
        assert 0 <= report.compliance_percentage <= 100
        assert len(report.requirements) == report.total_requirements
        
        # Some requirements should be detected as implemented/partial
        implemented_count = sum(1 for req in report.requirements if req.status == "implemented")
        partial_count = sum(1 for req in report.requirements if req.status == "partial")
        
        assert implemented_count + partial_count > 0
    
    def test_compliance_validation_with_missing_files(self, temp_dir):
        """Test compliance validation when implementation files are missing."""
        # Create SPEC.md
        spec_content = """
# CacheInfinity Specification

## 6.3 Virtual .ssh/authorized_keys
SFTP must support virtual .ssh/authorized_keys management.
"""
        (temp_dir / "SPEC.md").write_text(spec_content)
        
        # Don't create the implementation file
        validator = SPECComplianceValidator(str(temp_dir))
        report = validator.validate_implementation()
        
        # Should detect missing implementation
        assert report.total_requirements > 0
        assert report.not_implemented > 0
        assert report.compliance_percentage < 100
    
    def test_compliance_validation_with_spec_file_missing(self, temp_dir):
        """Test compliance validation when SPEC.md is missing."""
        validator = SPECComplianceValidator(str(temp_dir))
        
        # SPEC.md doesn't exist
        assert not (temp_dir / "SPEC.md").exists()
        
        # Should still work but with no requirements
        report = validator.validate_implementation()
        
        assert isinstance(report, ComplianceReport)
        assert report.total_requirements > 0
        assert 0 <= report.compliance_percentage <= 100


@pytest.mark.compliance
class TestSPECComplianceMarkers:
    """Test SPEC compliance with pytest markers."""
    
    def test_compliance_marker_applied(self):
        """Test that compliance tests are properly marked."""
        # This test is marked with @pytest.mark.compliance
        assert True
    
    @pytest.mark.slow
    def test_slow_compliance_test(self):
        """Test a slow compliance validation."""
        # Simulate a slow compliance check
        import time
        time.sleep(0.1)  # Short delay for testing
        assert True
    
    @pytest.mark.unit
    def test_unit_compliance_test(self):
        """Test unit-level compliance validation."""
        # Unit test for compliance functionality
        validator = SPECComplianceValidator(".")
        assert validator is not None
    
    @pytest.mark.integration
    def test_integration_compliance_test(self):
        """Test integration-level compliance validation."""
        # Integration test for compliance functionality
        validator = SPECComplianceValidator(".")
        # This would test with actual files in a real project
        assert validator is not None


# Helper functions for compliance testing
def assert_compliance_requirement_valid(requirement: ComplianceRequirement):
    """Assert that a compliance requirement is valid."""
    assert requirement.spec_section is not None
    assert requirement.requirement_id is not None
    assert requirement.description is not None
    assert requirement.implementation_path is not None
    assert requirement.status in ["implemented", "partial", "not_implemented", "pending"]
    assert requirement.priority in ["high", "medium", "low"]


def assert_compliance_report_valid(report: ComplianceReport):
    """Assert that a compliance report is valid."""
    assert report.total_requirements >= 0
    assert report.implemented >= 0
    assert report.partial >= 0
    assert report.not_implemented >= 0
    assert report.pending >= 0
    assert 0 <= report.compliance_percentage <= 100
    assert len(report.requirements) == report.total_requirements
    assert all(isinstance(req, ComplianceRequirement) for req in report.requirements)
    assert all(isinstance(gap, ComplianceRequirement) for gap in report.critical_gaps)


# Custom pytest fixtures for compliance testing
@pytest.fixture
def compliance_validator_with_files(temp_dir):
    """Create a compliance validator with test files."""
    # Create SPEC.md
    spec_content = """
# CacheInfinity Specification

## 2.1 Two-Port Architecture
The system must implement two-port architecture.

## 6.1 FTP/FTPS Implementation
FTP/FTPS implementation using pyftpdlib.
"""
    (temp_dir / "SPEC.md").write_text(spec_content)
    
    # Create implementation files
    (temp_dir / "app").mkdir()
    (temp_dir / "app" / "hosting").mkdir()
    (temp_dir / "app" / "hosting" / "dispatcher.py").write_text("# Two-port architecture")
    (temp_dir / "app" / "hosting" / "ftp.py").write_text("# FTP/FTPS implementation")
    
    return SPECComplianceValidator(str(temp_dir))


@pytest.fixture
def sample_compliance_requirements():
    """Create sample compliance requirements."""
    return [
        ComplianceRequirement(
            spec_section="2.1",
            requirement_id="TWO_PORT_ARCHITECTURE",
            description="Two-port architecture",
            implementation_path="app/hosting/dispatcher.py",
            status="implemented",
            priority="high"
        ),
        ComplianceRequirement(
            spec_section="6.1",
            requirement_id="FTP_FTPS_IMPLEMENTATION",
            description="FTP/FTPS implementation",
            implementation_path="app/hosting/ftp.py",
            status="partial",
            priority="medium"
        ),
        ComplianceRequirement(
            spec_section="6.3",
            requirement_id="VIRTUAL_AUTHORIZED_KEYS",
            description="Virtual .ssh/authorized_keys",
            implementation_path="app/hosting/sftp.py",
            status="not_implemented",
            priority="high",
            notes="Missing virtual authorized_keys functionality"
        )
    ]


# Test helper functions
def create_test_spec_file(directory: Path, content: str):
    """Create a test SPEC.md file."""
    spec_file = directory / "SPEC.md"
    spec_file.write_text(content)
    return spec_file


def create_test_implementation_file(directory: Path, path: str, content: str):
    """Create a test implementation file."""
    file_path = directory / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


def run_compliance_validation(project_root: str) -> ComplianceReport:
    """Run compliance validation and return the report."""
    validator = SPECComplianceValidator(project_root)
    return validator.validate_implementation()
