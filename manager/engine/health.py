from __future__ import annotations

import urllib.error
import urllib.request


class HealthCheckService:
    def check(self, base_url: str, path: str, timeout: int = 12) -> dict:
        url = base_url.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return {"healthy": 200 <= response.status < 300, "status": response.status, "url": url}
        except urllib.error.HTTPError as error:
            # 401 on an authenticated session endpoint means the backend is active, running, and protecting routes
            is_healthy = 200 <= error.code < 400 or (error.code == 401 and "session" in path)
            return {"healthy": is_healthy, "status": error.code, "url": url, "error": str(error) if not is_healthy else None}
        except (urllib.error.URLError, TimeoutError) as error:
            return {"healthy": False, "status": None, "url": url, "error": str(error)}
