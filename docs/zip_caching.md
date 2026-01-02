# Zip Caching Implementation

This document describes the zip caching functionality implemented in CacheInfinity, which provides intelligent caching and extraction of zip files from remote sources.

## Overview

The zip caching system automatically handles zip files downloaded from remote sources, providing two caching modes:

1. **Whole-zip caching**: Downloads and extracts the entire zip file to the staging area
2. **Individual-file caching**: Downloads the zip file and extracts only the requested file

The system automatically selects the appropriate mode based on file size limits and configuration settings.

## Configuration

### Configuration Options

Zip caching behavior is controlled through the following configuration options in `app/core/config.py`:

```python
# Maximum size for whole-zip caching (in GB)
max_zip_total_gb: int = 100

# Enable one-zip-at-a-time processing to prevent resource conflicts
one_zip_cache_at_a_time: bool = False
```

### Size Limits

- **Compressed size limit**: Maximum size of the downloaded zip file
- **Uncompressed size limit**: Maximum total size of all files when extracted
- Both limits are controlled by the `max_zip_total_gb` configuration option

### Locking Behavior

When `one_zip_cache_at_a_time` is enabled:
- Only one zip file can be processed at a time
- Other zip operations will wait for the current operation to complete
- This prevents resource conflicts and excessive memory usage

## Architecture

### Components

The zip caching system consists of several key components:

#### 1. StagingArea
Located in [`app/storage/staging.py`](../app/storage/staging.py)

The main staging area manager that provides:
- File staging and temporary file management
- Space management and cleanup
- Access to the zip cache manager

#### 2. ZipCacheManager
Located in [`app/storage/staging.py`](../app/storage/staging.py)

The core zip caching logic that handles:
- Size validation and mode selection
- Zip file download and extraction
- Locking and concurrency control
- Error handling and cleanup

#### 3. Configuration Integration
Located in [`app/core/config.py`](../app/core/config.py)

Provides configuration options that control:
- Size limits for zip caching
- Locking behavior
- Integration with the overall system configuration

## Usage

### Basic Usage

To use the zip caching functionality:

```python
from app.storage.staging import StagingArea, StagingDefinition
from app.core.config import Config

# Create staging area
staging_def = StagingDefinition(size_gb=50)
staging = StagingArea(staging_def)

# Get zip cache manager with configuration
config = Config()
limits = {
    "max_zip_total_gb": config.max_zip_total_gb,
    "one_zip_cache_at_a_time": config.one_zip_cache_at_a_time
}
zip_manager = staging.get_zip_cache_manager(limits)

# Handle a zip file
result = zip_manager.handle_zip_file(
    zip_url="https://example.com/file.zip",
    destination=Path("/path/to/destination/file.txt"),
    member_path="file.txt"
)
```

### Mode Selection

The system automatically selects the caching mode based on:

1. **Size validation**: Checks if the zip file and its contents fit within configured limits
2. **Lock availability**: If one-zip-at-a-time is enabled, checks if a lock can be acquired
3. **Configuration**: Respects user preferences for caching behavior

### Error Handling

The zip caching system includes comprehensive error handling:

- **Download failures**: Graceful handling of network issues
- **Size limit violations**: Clear logging when files exceed limits
- **Lock conflicts**: Proper waiting and retry logic
- **Extraction errors**: Cleanup and error reporting
- **File system issues**: Space checking and cleanup

## Integration Points

### WebDAV Integration

The zip caching system is designed to integrate with WebDAV and other file serving systems. When a zip file is requested:

1. The system detects the zip file type
2. Uses the zip cache manager to handle the download and extraction
3. Serves the extracted file to the client
4. Caches the results for future requests

### Fetcher Integration

The zip caching system integrates with the fetcher service for downloading zip files:

- Downloads zip files to the staging area
- Handles authentication and authorization
- Manages download progress and retries
- Provides integration points for different protocols

## Performance Considerations

### Memory Usage

- **Whole-zip mode**: Requires memory for the entire zip file and extracted contents
- **Individual-file mode**: Only requires memory for the specific file being extracted
- **Locking**: Prevents multiple large extractions simultaneously

### Disk Usage

- **Staging area**: Temporary storage for downloaded zip files
- **Cache area**: Persistent storage for extracted files
- **Cleanup**: Automatic cleanup of old and unused files

### Network Usage

- **Whole-zip mode**: Downloads entire zip file regardless of which files are needed
- **Individual-file mode**: Downloads entire zip file but only extracts specific files
- **Caching**: Reduces repeated downloads of the same zip files

## Testing

### Test Coverage

The zip caching system includes comprehensive tests in [`test_zip_caching.py`](../test_zip_caching.py):

- **Size validation**: Tests size limit enforcement
- **Locking mechanism**: Tests concurrent access handling
- **Mode selection**: Tests automatic mode selection logic
- **Error handling**: Tests various failure scenarios

### Running Tests

```bash
# Run the zip caching tests
.venv/bin/python test_zip_caching.py

# Run with pytest for more detailed output
.venv/bin/python -m pytest test_zip_caching.py -v
```

## Future Enhancements

### Planned Features

1. **Streaming extraction**: Extract files without downloading entire zip
2. **Parallel processing**: Handle multiple zip files concurrently with resource limits
3. **Compression optimization**: Use different compression levels based on file types
4. **Cache invalidation**: Smart cache cleanup based on usage patterns
5. **Metrics and monitoring**: Track performance and usage statistics

### Integration Improvements

1. **Protocol support**: Better integration with different download protocols
2. **Authentication**: Enhanced authentication and authorization for zip downloads
3. **Caching strategies**: More sophisticated caching algorithms
4. **Resource management**: Better memory and disk usage management

## Troubleshooting

### Common Issues

1. **"Zip compressed size exceeds limit"**: File is too large for configured limits
   - Solution: Increase `max_zip_total_gb` in configuration

2. **"Failed to acquire zip lock"**: Another zip operation is in progress
   - Solution: Wait for current operation to complete or disable one-zip-at-a-time

3. **"No member path specified"**: Individual file extraction requires a specific file path
   - Solution: Provide the `member_path` parameter

4. **"Insufficient space"**: Not enough disk space for staging or extraction
   - Solution: Clean up staging area or increase disk space

### Debug Logging

Enable debug logging to troubleshoot issues:

```python
import logging
logging.getLogger('app.storage.staging').setLevel(logging.DEBUG)
```

This will provide detailed information about:
- Size calculations and limit checks
- Lock acquisition and release
- Download and extraction progress
- Error conditions and recovery

## Security Considerations

### File Path Validation

- All file paths are validated to prevent directory traversal attacks
- Zip file contents are extracted to safe locations
- File permissions are properly set

### Resource Limits

- Size limits prevent denial-of-service attacks
- Locking prevents resource exhaustion
- Cleanup prevents disk space exhaustion

### Access Control

- Zip downloads respect existing authentication and authorization
- File access follows standard CacheInfinity permissions
- Staging area is properly secured