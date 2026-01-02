#!/usr/bin/env python3
"""
CacheInfinity Automated Compliance Checking Script

This script provides automated compliance checking as part of the development
workflow. It can be integrated into CI/CD pipelines, pre-commit hooks, or run
manually to validate SPEC compliance.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
import json
import yaml


class ComplianceChecker:
    """Automated compliance checking for CacheInfinity."""
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.validator_script = self.project_root / "app" / "utils" / "compliance_validator.py"
        
    def run_compliance_check(self, output_format: str = "text", output_file: Optional[str] = None) -> bool:
        """Run compliance validation and return success status."""
        if not self.validator_script.exists():
            print("❌ Compliance validator script not found")
            return False
        
        # Run the compliance validator
        cmd = [sys.executable, str(self.validator_script), "--format", output_format]
        if output_file:
            cmd.extend(["--output", output_file])
        
        try:
            result = subprocess.run(cmd, cwd=self.project_root, capture_output=True, text=True)
            
            if result.returncode == 0:
                print("✅ Compliance check completed successfully")
                if result.stdout:
                    print(result.stdout)
                return True
            else:
                print("❌ Compliance check failed")
                if result.stderr:
                    print(f"Error: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ Error running compliance check: {e}")
            return False
    
    def check_critical_gaps(self) -> List[Dict[str, Any]]:
        """Check for critical compliance gaps."""
        if not self.validator_script.exists():
            return []
        
        try:
            cmd = [sys.executable, str(self.validator_script), "--format", "json"]
            result = subprocess.run(cmd, cwd=self.project_root, capture_output=True, text=True)
            
            if result.returncode == 0:
                report = json.loads(result.stdout)
                return report.get("critical_gaps", [])
            else:
                return []
                
        except Exception:
            return []
    
    def generate_compliance_summary(self) -> Dict[str, Any]:
        """Generate a compliance summary for CI/CD."""
        if not self.validator_script.exists():
            return {
                "status": "error",
                "message": "Compliance validator not found",
                "compliance_percentage": 0,
                "critical_gaps": 0,
                "total_requirements": 0
            }
        
        try:
            cmd = [sys.executable, str(self.validator_script), "--format", "json"]
            result = subprocess.run(cmd, cwd=self.project_root, capture_output=True, text=True)
            
            if result.returncode == 0:
                report = json.loads(result.stdout)
                return {
                    "status": "success",
                    "compliance_percentage": report.get("compliance_percentage", 0),
                    "critical_gaps": len(report.get("critical_gaps", [])),
                    "total_requirements": report.get("total_requirements", 0),
                    "implemented": report.get("implemented", 0),
                    "partial": report.get("partial", 0),
                    "not_implemented": report.get("not_implemented", 0)
                }
            else:
                return {
                    "status": "error",
                    "message": "Compliance check failed",
                    "compliance_percentage": 0,
                    "critical_gaps": 0,
                    "total_requirements": 0
                }
                
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error: {str(e)}",
                "compliance_percentage": 0,
                "critical_gaps": 0,
                "total_requirements": 0
            }
    
    def create_pre_commit_hook(self) -> bool:
        """Create a pre-commit hook for compliance checking."""
        hook_content = f'''#!/bin/sh
# CacheInfinity Pre-commit Hook
# Automatically runs compliance validation before commits

echo "🔍 Running CacheInfinity compliance check..."

# Run compliance validation
python3 "{self.validator_script}" --format json > /tmp/compliance_check.json

# Check if compliance check passed
if [ $? -eq 0 ]; then
    # Parse JSON to check for critical gaps
    CRITICAL_GAPS=$(python3 -c "
import json
with open('/tmp/compliance_check.json') as f:
    data = json.load(f)
    print(len(data.get('critical_gaps', [])))
")
    
    if [ "$CRITICAL_GAPS" -gt 0 ]; then
        echo "❌ Commit blocked: Found $CRITICAL_GAPS critical compliance gaps"
        echo "Please address critical gaps before committing:"
        python3 -c "
import json
with open('/tmp/compliance_check.json') as f:
    data = json.load(f)
    for gap in data.get('critical_gaps', []):
        print(f'  - {gap["requirement_id"]}: {gap["description"]}')
"
        exit 1
    else
        echo "✅ Compliance check passed - no critical gaps found"
        exit 0
    fi
else
    echo "❌ Compliance check failed"
    exit 1
fi
'''
        
        # Create .git/hooks directory if it doesn't exist
        hooks_dir = self.project_root / ".git" / "hooks"
        if not hooks_dir.exists():
            hooks_dir.mkdir(parents=True)
        
        # Write pre-commit hook
        pre_commit_hook = hooks_dir / "pre-commit"
        with open(pre_commit_hook, 'w') as f:
            f.write(hook_content)
        
        # Make it executable
        os.chmod(pre_commit_hook, 0o755)
        
        print(f"✅ Pre-commit hook created at {pre_commit_hook}")
        return True
    
    def setup_ci_integration(self) -> bool:
        """Set up CI/CD integration for compliance checking."""
        # Create GitHub Actions workflow
        workflow_dir = self.project_root / ".github" / "workflows"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        
        workflow_content = '''name: CacheInfinity Compliance Check

on:
  push:
    branches: [ main, devel/* ]
  pull_request:
    branches: [ main ]

jobs:
  compliance-check:
    runs-on: ubuntu-latest
    
    steps:
    - name: Checkout code
      uses: actions/checkout@v4
      
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.10'
        
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install pyyaml
        
    - name: Run compliance check
      run: |
        python app/utils/compliance_validator.py --format json > compliance_report.json
        
    - name: Check compliance status
      run: |
        python -c "
import json
with open('compliance_report.json') as f:
    data = json.load(f)
    
print(f'Compliance: {data[\"compliance_percentage\"]:.1f}%')
print(f'Critical gaps: {len(data[\"critical_gaps\"])}')
print(f'Total requirements: {data[\"total_requirements\"]}')

if len(data[\"critical_gaps\"]) > 0:
    print('❌ Build failed due to critical compliance gaps')
    exit(1)
else:
    print('✅ Compliance check passed')
"
        
    - name: Upload compliance report
      uses: actions/upload-artifact@v3
      with:
        name: compliance-report
        path: compliance_report.json
'''
        
        workflow_file = workflow_dir / "compliance-check.yml"
        with open(workflow_file, 'w') as f:
            f.write(workflow_content)
        
        print(f"✅ GitHub Actions workflow created at {workflow_file}")
        return True


def main():
    """Main entry point for compliance checking."""
    parser = argparse.ArgumentParser(description="CacheInfinity automated compliance checking")
    parser.add_argument("--check", action="store_true", help="Run compliance check")
    parser.add_argument("--summary", action="store_true", help="Generate compliance summary")
    parser.add_argument("--setup-pre-commit", action="store_true", help="Set up pre-commit hook")
    parser.add_argument("--setup-ci", action="store_true", help="Set up CI/CD integration")
    parser.add_argument("--format", choices=["text", "json", "yaml"], default="text",
                       help="Output format for compliance check")
    parser.add_argument("--output", "-o", help="Output file for compliance check")
    parser.add_argument("--project-root", default=".", help="Project root directory")
    
    args = parser.parse_args()
    
    checker = ComplianceChecker(args.project_root)
    
    if args.check:
        success = checker.run_compliance_check(args.format, args.output)
        sys.exit(0 if success else 1)
    
    elif args.summary:
        summary = checker.generate_compliance_summary()
        if summary["status"] == "success":
            print(f"Compliance: {summary['compliance_percentage']:.1f}%")
            print(f"Critical gaps: {summary['critical_gaps']}")
            print(f"Total requirements: {summary['total_requirements']}")
            print(f"Implemented: {summary['implemented']}")
            print(f"Partial: {summary['partial']}")
            print(f"Not implemented: {summary['not_implemented']}")
        else:
            print(f"❌ Error: {summary['message']}")
        sys.exit(0 if summary["status"] == "success" else 1)
    
    elif args.setup_pre_commit:
        success = checker.create_pre_commit_hook()
        sys.exit(0 if success else 1)
    
    elif args.setup_ci:
        success = checker.setup_ci_integration()
        sys.exit(0 if success else 1)
    
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()