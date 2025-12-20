# Mermaid Diagrams for CacheInfinity Initialization Flow

## Current vs Target Architecture Comparison

### Current Architecture (Monolithic)
```mermaid
graph TD
    A[app/cacheinfinity.py] --> B[core.server.main]
    B --> C[CacheInfinityService.__init__]
    C --> D[apply_settings - Monolithic Block]
    D --> E[Initialize Database]
    D --> F[Initialize Config]
    D --> G[Initialize Logging]
    D --> H[Initialize Auth]
    D --> I[Initialize TLS]
    D --> J[Initialize WebUI]
    D --> K[Initialize Indexer]
    E --> L[Create WSGI App]
    F --> L
    G --> L
    H --> L
    I --> L
    J --> L
    K --> L
    L --> M[Start Server]
    
    style A fill:#e1f5fe
    style B fill:#f3e5f5
    style C fill:#fff3e0
    style D fill:#ffebee
    style M fill:#e8f5e9
```

### Target Architecture (Service-Oriented)
```mermaid
graph TD
    A[app/cacheinfinity.py] --> B[core.server.main]
    B --> C[ServiceManager]
    C --> D[Initialize DatabaseManager]
    C --> E[Initialize ConfigManager]
    C --> F[Initialize LoggingManager]
    C --> G[Initialize AuthManager]
    C --> H[Initialize TLSManager]
    C --> I[Initialize DatabaseManager]
    C --> J[Initialize IndexerManager]
    C --> K[Initialize WebUIService]
    
    D --> L[Database Connection]
    L --> M[Schema Creation]
    
    E --> N[Load Config from DB]
    N --> O[Config Validation]
    
    F --> P[Configure Logging]
    P --> Q[Log Rotation Setup]
    
    G --> R[Load Users]
    R --> S[Auth Providers]
    
    H --> T[Load Certificates]
    T --> U[TLS Configuration]
    
    I --> V[Data Adapters]
    V --> W[Data Validation]
    
    J --> X[Remote Fetcher]
    X --> Y[Indexing Schedules]
    
    K --> Z[Web Server]
    Z --> AA[API Endpoints]
    
    M --> BB[Service Context]
    O --> BB
    Q --> BB
    S --> BB
    U --> BB
    W --> BB
    Y --> BB
    AA --> BB
    
    BB --> CC[Create WSGI App]
    CC --> DD[Start Server]
    
    style A fill:#e1f5fe
    style B fill:#f3e5f5
    style C fill:#e8f5e9
    style DD fill:#fff3e0
```

## Service Dependencies

### Service Dependency Graph
```mermaid
graph LR
    DB[DatabaseManager] --> CM[ConfigManager]
    DB --> AM[AuthManager]
    DB --> DM[DatabaseManager]
    DB --> IM[IndexerManager]
    
    CM --> LM[LoggingManager]
    CM --> AM
    CM --> TM[TLSManager]
    CM --> DM
    CM --> IM
    CM --> WS[WebUIService]
    
    LM --> AM
    LM --> TM
    LM --> DM
    LM --> IM
    LM --> WS
    
    AM --> TM
    AM --> DM
    AM --> IM
    AM --> WS
    
    TM --> DM
    TM --> IM
    TM --> WS
    
    DM --> IM
    DM --> WS
    
    IM --> WS
    
    style DB fill:#e8f5e9
    style CM fill:#fff3e0
    style WS fill:#ffebee
```

## Service Lifecycle

### Service Lifecycle Management
```mermaid
stateDiagram-v2
    [*] --> Initializing: ServiceManager.initialize()
    Initializing --> Initialized: initialize() success
    Initializing --> Failed: initialize() error
    
    Initialized --> Starting: ServiceManager.start()
    Starting --> Started: start() success
    Starting --> Failed: start() error
    
    Started --> Stopping: ServiceManager.stop()
    Stopping --> Stopped: stop() success
    Stopping --> Failed: stop() error
    
    Failed --> [*]: Critical service
    Stopped --> [*]: Normal shutdown
    
    note right of Initializing
        - Load dependencies from context
        - Initialize service components
        - Validate configuration
    end note
    
    note right of Starting
        - Start background tasks
        - Open network connections
        - Register with service registry
    end note
    
    note right of Stopping
        - Stop background tasks
        - Close network connections
        - Clean up resources
    end note
```

## Service Manager Flow

### Service Manager Initialization Flow
```mermaid
flowchart TD
    A[ServiceManager.initialize_all] --> B[Build dependency graph]
    B --> C[Validate dependencies]
    C --> D{Any cycles?}
    D -->|Yes| E[Throw DependencyCycleError]
    D -->|No| F[Initialize services in order]
    
    F --> G{Service: DatabaseManager}
    G -->|Yes| H[Initialize DatabaseManager]
    G -->|No| I{Service: ConfigManager}
    I -->|Yes| J[Initialize ConfigManager]
    I -->|No| K{Service: LoggingManager}
    K -->|Yes| L[Initialize LoggingManager]
    K -->|No| M{Service: AuthManager}
    M -->|Yes| N[Initialize AuthManager]
    M -->|No| O{Service: TLSManager}
    O -->|Yes| P[Initialize TLSManager]
    O -->|No| Q{Service: DatabaseManager}
    Q -->|Yes| R[Initialize DatabaseManager]
    Q -->|No| S{Service: IndexerManager}
    S -->|Yes| T[Initialize IndexerManager]
    S -->|No| U{Service: WebUIService}
    U -->|Yes| V[Initialize WebUIService]
    U -->|No| W[Unknown service]
    
    H --> X[Add to context]
    J --> X
    L --> X
    N --> X
    P --> X
    R --> X
    T --> X
    V --> X
    
    X --> Y{More services?}
    Y -->|Yes| F
    Y -->|No| Z[All services initialized]
    
    E --> AA[Log error]
    W --> BB[Log warning]
    
    AA --> CC[Return failure]
    BB --> Z
    Z --> DD[Return success]
    
    style A fill:#e8f5e9
    style Z fill:#fff3e0
    style CC fill:#ffebee
    style DD fill:#e8f5e9
```

## Error Handling Strategy

### Error Handling Flow
```mermaid
graph TD
    A[Service Initialization] --> B{Success?}
    B -->|Yes| C[Continue to next service]
    B -->|No| D[Log error with context]
    
    D --> E{Critical service?}
    E -->|Yes| F[Stop initialization]
    E -->|No| G[Log warning]
    
    G --> H{Continue on error?}
    H -->|Yes| C
    H -->|No| F
    
    F --> I[Rollback initialized services]
    I --> J[Return failure]
    
    C --> K{More services?}
    K -->|Yes| A
    K -->|No| L[All services initialized]
    
    L --> M[Start all services]
    M --> N{Success?}
    N -->|Yes| O[Return success]
    N -->|No| P[Stop started services]
    P --> Q[Return partial failure]
    
    style A fill:#fff3e0
    style F fill:#ffebee
    style O fill:#e8f5e9
    style J fill:#ffebee
```

## Service Context

### Service Context Structure
```mermaid
graph TB
    A[Service Context] --> B[DatabaseManager]
    A --> C[ConfigManager]
    A --> D[LoggingManager]
    A --> E[AuthManager]
    A --> F[TLSManager]
    A --> G[DatabaseManager]
    A --> H[IndexerManager]
    A --> I[WebUIService]
    
    B --> B1[Database Connection]
    B --> B2[Schema Version]
    B --> B3[Connection Pool]
    
    C --> C1[Configuration Data]
    C --> C2[Config Version]
    C --> C3[Validation Status]
    
    D --> D1[Logger Instance]
    D --> D2[Log Level]
    D --> D3[Log Handlers]
    
    E --> E1[Auth Providers]
    E --> E2[User Manager]
    E --> E3[Session Store]
    
    F --> F1[Certificate Info]
    F --> F2[TLS Config]
    F --> F3[Renewal Status]
    
    G --> G1[Data Adapters]
    G --> G2[Validation Rules]
    G --> G3[Cache Layer]
    
    H --> H1[Fetcher Instance]
    H --> H2[Indexing Schedules]
    H --> H3[Hotness Tracker]
    
    I --> I1[Web Server]
    I --> I2[API Routes]
    I --> I3[Auth Middleware]
    
    style A fill:#e8f5e9
    style B fill:#fff3e0
    style C fill:#fff3e0
    style D fill:#fff3e0
    style E fill:#fff3e0
    style F fill:#fff3e0
    style G fill:#fff3e0
    style H fill:#fff3e0
    style I fill:#fff3e0
```

## Migration Timeline

### Migration Phases
```mermaid
gantt
    title CacheInfinity Initialization Flow Migration
    dateFormat  YYYY-MM-DD
    section Foundation
    BaseService Interface       :a1, 2024-01-01, 7d
    ServiceManager              :a2, after a1, 10d
    Exception Hierarchy         :a3, after a1, 5d
    section Core Services
    DatabaseManager             :b1, after a2, 10d
    ConfigManager               :b2, after b1, 10d
    LoggingManager              :b3, after b2, 7d
    section Authentication
    AuthManager                 :c1, after b2, 14d
    TLSManager                  :c2, after c1, 10d
    section Data Layer
    DatabaseManager             :d1, after b1, 14d
    IndexerManager              :d2, after d1, 14d
    section Web Interface
    WebUIService                :e1, after c2, 14d
    Integration Testing         :e2, after e1, 10d
    section Deployment
    Performance Testing         :f1, after e2, 7d
    Documentation               :f2, after e2, 10d
    Production Deployment       :f3, after f1 f2, 5d