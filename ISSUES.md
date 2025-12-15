# Issues

Tracked here are known bugs or operational hazards that affect the current build.
See `TODO.md` for larger feature work that is still outstanding.

## Open items

1. **WebUI sessions evaporate on restart**
   - *Symptom:* Logging back into the WebUI is required after every service
     restart or worker crash because session data lives only in-process.
   - *Impact:* Admin actions in progress are interrupted, and horizontal scaling
     (multiple UI workers) is not possible.
   - *Next steps:* Replace the in-memory session dict with a persistent store
     (database table or signed tokens) shared across processes.

2. **`psycopg.OperationalError: the connection is closed` during WebUI traffic**
   - *Symptom:* After periods of inactivity, WebUI/API calls may crash because
     the single psycopg connection has been closed by PostgreSQL. The traceback
     surfaces inside `db_adapter.execute`.
   - *Impact:* Requests fail and the UI may become unresponsive until the
     process is restarted.
   - *Next steps:* Introduce a proper connection pool (e.g., psycopg connection
     pool or SQLAlchemy) or automatic reconnect logic in `DBAdapter`.

3. **WebUI remains unresponsive after successful login**
   - *Symptom:* Even on the latest build (with absolute `/api/...` fetches), the
     dashboard does not load data after entering valid credentials—the UI sits
     idle and API requests appear to stall.
   - *Impact:* Administrators cannot manage the system through the WebUI.
   - *Status:* Investigation is being handed off to another engineer/LLM; no fix
     attempted in this change.
