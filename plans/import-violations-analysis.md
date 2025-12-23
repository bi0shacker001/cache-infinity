# Import Violations Analysis (Final)

Based on the analysis of import statements and the clarified import rules, here are the current violations:

## Final Import Rules

### 1. Database Module Rules
- `db.dbmanage` **cannot** be imported by `core.server`
- `db.backupmgmt` import by `db.dbmanage` is **valid** (closely related database services)
- `db.backupmgmt` **should not** be imported by `ui.backend` - calls should be routed through `db.dbmanage`

### 2. Authentication Module Rules  
- `auth.credentials` **cannot** be imported by `core.server`
- `auth.credentials` **can** be imported by `ui.backend` (this is allowed)

## Current Violations

### Critical Violations (Must Be Fixed)

#### 1. core.server Import Violations
**File**: `app/core/server.py`
- **Line 25**: `from auth.credentials import AuthConfigManager` - **VIOLATION**: core.server cannot import auth.credentials
- **Line 43**: `from db.dbmanage import DatabaseManager, load_database_settings` - **VIOLATION**: core.server cannot import db.dbmanage

#### 2. ui.backend Import Issue (Architectural)
**File**: `app/ui/backend.py`
- **Line 34**: `from ..db.backupmgmt import DatabaseBackupManager` - **ARCHITECTURAL ISSUE**: Should be routed through db.dbmanage since this is db<->ui communication, not db<->disk

### Valid Imports (No Longer Violations)

#### ui.backend auth.credentials Import
**File**: `app/ui/backend.py`
- **Line 14**: `from ..auth.credentials import get_cli_api_key` - **VALID**: ui.backend is allowed to import auth.credentials

## Recommended Fixes

### 1. Fix core.server Violations
**Action**: Remove direct imports from `auth.credentials` and `db.dbmanage`
**Approach**: 
- Use dependency injection or service locator pattern
- Access functionality through approved interfaces
- Create abstraction layers for required functionality

### 2. Fix ui.backend Backup Routing
**Action**: Route `db.backupmgmt` calls through `db.dbmanage`
**Approach**:
- Replace direct `db.backupmgmt` import with `db.dbmanage` mediated calls
- Create backup service interface in `db.dbmanage` if needed
- Ensure proper separation between db<->disk and db<->ui communication

### 3. Architectural Improvements
**Action**: Review and refactor database access patterns
**Approach**:
- Create proper abstraction layers for database access
- Implement service interfaces that can be safely imported
- Use dependency injection throughout the codebase
- Consider creating a database service facade

## Implementation Plan

### Phase 1: Fix Critical Violations
1. **Refactor core.server** to remove prohibited imports from `auth.credentials` and `db.dbmanage`
2. **Refactor ui.backend** to route backup calls through `db.dbmanage`
3. **Create abstraction layers** for authentication and database access

### Phase 2: Review Other Database Access
1. **Analyze** other modules importing `db.dbmanage`:
   - `core.config.py` (line 17)
   - `core.services.py` (line 19) 
   - `net.indexer.py` (line 21)
   - `auth.credentials.py` (line 23)
2. **Determine** if they should have direct access or use abstractions
3. **Refactor** as needed to comply with architectural principles

### Phase 3: Implement Proper Architectural Patterns
1. **Create service interfaces** for cross-module communication
2. **Implement dependency injection** container
3. **Document** proper import patterns and restrictions
4. **Add architectural tests** to prevent future violations