"""
services/erpnext_client.py

The ONLY place in this project that talks to ERPNext.
Every other module imports `erpnext` from here — never builds its own HTTP calls.

Key design decisions:
- Async (httpx.AsyncClient) — compatible with FastAPI's async runtime
- _handle_response() guards against non-JSON responses (ERPNext returns HTML on 500s)
- ERPNextError carries both status code and message for clean upstream handling
- Single shared client instance (module-level singleton)
"""

import httpx
import logging
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


class ERPNextError(Exception):
    """
    Raised whenever ERPNext returns an error (4xx/5xx) or a non-JSON body.
    Carries the HTTP status code and a human-readable message extracted from
    ERPNext's error envelope, so callers can decide how to handle it.
    """

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"ERPNext [{status_code}]: {message}")


class ERPNextClient:
    """
    Reusable async HTTP client for the ERPNext REST API.

    Usage:
        from services.erpnext_client import erpnext
        data = await erpnext.get("/api/resource/Employee")
    """

    def __init__(self):
        # Strip trailing slash so we can always safely append paths
        self.base_url = settings.erpnext_base_url.rstrip("/")

        # ERPNext token auth: "token api_key:api_secret"
        self.headers = {
            "Authorization": (
                f"token {settings.erpnext_api_key}:{settings.erpnext_api_secret}"
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """
        HTTP GET against the ERPNext REST API.

        Args:
            path:   API path, e.g. "/api/resource/Salary Slip"
            params: Query parameters dict (filters, fields, limit, etc.)

        Returns:
            Parsed JSON dict from ERPNext.

        Raises:
            ERPNextError on any HTTP error or non-JSON response.
        """
        url = f"{self.base_url}{path}"
        logger.debug("GET %s | params=%s", url, params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers, params=params)

        return self._handle_response(response)

    async def post(self, path: str, body: dict[str, Any] | None = None) -> dict:
        """
        HTTP POST against the ERPNext REST API.

        Args:
            path: API path, e.g. "/api/resource/Salary Slip"
            body: JSON payload dict.

        Returns:
            Parsed JSON dict from ERPNext.

        Raises:
            ERPNextError on any HTTP error or non-JSON response.
        """
        url = f"{self.base_url}{path}"
        logger.debug("POST %s | body=%s", url, body)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url, headers=self.headers, json=body or {}
            )

        return self._handle_response(response)

    async def put(self, path: str, body: dict[str, Any] | None = None) -> dict:
        """
        HTTP PUT — used for updating existing ERPNext documents.
        """
        url = f"{self.base_url}{path}"
        logger.debug("PUT %s | body=%s", url, body)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                url, headers=self.headers, json=body or {}
            )

        return self._handle_response(response)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_response(self, response: httpx.Response) -> dict:
        """
        Central response handler.

        ERPNext does NOT always return JSON:
        - 500 errors often return an HTML traceback page
        - Auth failures (401/403) sometimes return plain text
        - Validation errors return JSON with an "exception" key

        This method handles all cases cleanly so callers never see a
        raw JSONDecodeError.
        """
        # Attempt JSON parsing — guard against HTML/text responses
        try:
            data = response.json()
        except Exception:
            # Response body is not JSON — likely HTML error page
            preview = response.text[:500] if response.text else "(empty body)"
            logger.error(
                "Non-JSON response from ERPNext [%d]: %s",
                response.status_code,
                preview,
            )
            raise ERPNextError(
                status_code=response.status_code,
                message=f"ERPNext returned a non-JSON response. "
                        f"Status: {response.status_code}. "
                        f"Body preview: {preview}",
            )

        # HTTP error with a JSON body — extract the error message
        if response.status_code >= 400:
            # ERPNext wraps errors in different keys depending on version
            error_message = (
                data.get("exception")
                or data.get("message")
                or data.get("_error_message")
                or f"HTTP {response.status_code} error"
            )
            logger.error(
                "ERPNext API error [%d]: %s", response.status_code, error_message
            )
            raise ERPNextError(
                status_code=response.status_code,
                message=str(error_message),
            )

        logger.debug("ERPNext response OK [%d]", response.status_code)
        return data

    # ------------------------------------------------------------------
    # Convenience helpers for common ERPNext patterns
    # ------------------------------------------------------------------

    async def get_list(
        self,
        doctype: str,
        filters: list | None = None,
        fields: list[str] | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """
        Fetch a list of ERPNext documents.

        Args:
            doctype:  ERPNext DocType name, e.g. "Salary Slip"
            filters:  List of filter tuples, e.g. [["docstatus","in","0,1"]]
            fields:   List of field names to return
            limit:    Max records to return (default 500)

        Returns:
            List of document dicts.
        """
        import json

        params: dict[str, Any] = {"limit_page_length": limit}

        if filters:
            params["filters"] = json.dumps(filters)

        if fields:
            params["fields"] = json.dumps(fields)

        result = await self.get(f"/api/resource/{doctype}", params=params)
        return result.get("data", [])

    async def get_document(self, doctype: str, name: str) -> dict:
        """
        Fetch a single ERPNext document by name.

        Args:
            doctype: ERPNext DocType name
            name:    Document name/ID

        Returns:
            Document dict (the "data" field from ERPNext response).
        """
        result = await self.get(f"/api/resource/{doctype}/{name}")
        return result.get("data", {})


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------
# Every module does:
#   from services.erpnext_client import erpnext
# Never instantiate ERPNextClient() directly in other files.
# ---------------------------------------------------------------------------
erpnext = ERPNextClient()