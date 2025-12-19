app:
  auth:
    - credentials.py
    - tls.py
  cache:
    - cachelinks.py
    - checksum.py
  core:
    - config.py
    - errors.py
    - logging.py
    - server.py
    - service.py
  db:
    - adapter.py
    - backupmgmt.py
    - dbmanage.py
    - index.py
    backends:
      - postgresql.py
      - redis.py
      - sqlite.py
  hosting:
    - browser_interface.py
    - webdav.py
  net:
    - fetcher.py
    - indexer.py
  storage:
    - backend.py
    - configuration.py
    - staging.py
  ui:
    - api.py
    - cli.py
    - management.py
    web:
      - webcore.py
      assets:
        css:
          - components.css
          - layout.css
          - styles.css
        js:
          - cachelinks.js
          - common.js
          - cookies.js
          - maintenance.js
          - overview.js
          - settings.js
          - storage.js
          - users.js
        pages:
          - cachelinks.html
          - cookies.html
          - index.html
          - login.html
          - maintenance.html
          - overview.html
          - settings.html
          - storage.html
          - users.html
  utils:
    - filemanager.py