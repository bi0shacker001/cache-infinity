# Codebase Reorganization Plan: Server and Services Structure

## Overview
Reorganize the codebase to clearly separate server responsibilities from service management responsibilities, and standardize all service files to follow consistent patterns.

## Current Issues

### server.py Problems
- Contains extensive service initialization logic that belongs in services.py
- Has direct imports of UI and hosting modules (violates separation of concerns)
- Handles service lifecycle management instead of delegating to services.py
- Mixes HTTP server concerns with application logic

### services.py Problems  
- Service classes exist but aren't used consistently by server.py
- Missing standardized service lifecycle patterns
- Incomplete service dependency management

### Service Files Problems
- Inconsistent structure across different service files
- Missing standardized start/stop/restart patterns
- No clear service lifecycle interfaces

## Reorganization Requirements

### 1. server.py - Server Responsibilities Only
**REMOVE from server.py:**
- Service initialization logic (lines 2171-2185)
- Direct imports of UI and hosting modules
- Service lifecycle management code
- Application-specific logic

**KEEP in server.py:**
- Main server loop and HTTP server management
- Signal handling (SIGHUP, SIGUSR1, SIGTERM, SIGINT)
- Process management (daemon mode, PID file management)
- Service manager orchestration (start/stop/restart calls)
- WSGI application routing

### 2. services.py - Service Management Only
**ENHANCE services.py:**
- Complete service lifecycle management (initialize, start, stop, restart)
- Service dependency resolution and ordering
- Service registration and coordination
- Application service composition
- Service restart capabilities

### 3. Standardized Service Structure
**Each service file must have:**
- Service class inheriting from BaseService
- `initialize(context)` method for dependency injection
- `start()` method for service startup
- `stop()` method for cleanup
- Clear dependencies list
- Consistent error handling

**Restart functionality will be handled by services.py:**
- ServiceManager will provide restart capability
- Restart will use stop() then start() sequence
- No individual restart() methods needed on services

## Implementation Plan

### Phase 1: Extract Service Logic from server.py
1. Move service initialization from server.py to services.py
2. Remove direct UI/hosting imports from server.py
3. Create service manager orchestration in server.py
4. Update server.py to use services.py for all service operations

### Phase 2: Enhance services.py
1. Add restart capability to BaseService
2. Improve service dependency management
3. Add service health monitoring
4. Standardize service error handling

### Phase 3: Standardize Service Files
1. Review all service files for consistency
2. Add missing lifecycle methods where needed
3. Standardize dependency declarations
4. Ensure consistent error handling patterns

### Phase 4: Update Integration Points
1. Update server.py to use new service patterns
2. Test service restart functionality
3. Validate signal handling works with new structure
4. Ensure all existing functionality is preserved

## Service Lifecycle Patterns

### BaseService Interface
```python
class BaseService(ABC):
    name: str
    dependencies: tuple[str, ...] = ()
    
    def initialize(self, context: dict[str, Any]) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

### Service Manager Enhancements
```python
class ServiceManager:
    def restart_all(self) -> None: ...
    def restart_service(self, name: str) -> None: ...
    def get_service_health(self, name: str) -> dict: ...
```

## Validation Criteria

### After Reorganization:
- [ ] server.py contains only server-related logic
- [ ] All service initialization happens in services.py
- [ ] All service files follow standardized structure
- [ ] Service restart functionality works correctly
- [ ] Signal handling preserves service state
- [ ] All existing functionality is maintained
- [ ] Service dependencies are properly resolved
- [ ] Error handling is consistent across services

## Dependencies and Order
1. Phase 1 must complete before Phase 2
2. Phase 2 must complete before Phase 3
3. Phase 3 must complete before Phase 4
4. Each phase should be tested before proceeding

## Risk Mitigation
- Maintain backward compatibility during transition
- Add comprehensive logging for service lifecycle events
- Implement graceful degradation for service failures
- Ensure proper cleanup on service stop/restart
- Test signal handling thoroughly