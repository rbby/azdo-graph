from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    _VERSION = _dist_version("ado-pipeline-graph")
except PackageNotFoundError:
    _VERSION = "0"

RETRY_STATUSES = (429, 502, 503, 504)
MAX_RETRY_DELAY = 60.0

_STATUS_MESSAGES = {
    401: "authentication failed; check ADO_PAT",
    403: "access denied; check project and pipeline permissions",
    404: "the requested Azure DevOps resource was not found",
}


class AdoApiError(RuntimeError):
    """An Azure DevOps Services API request failed."""


class _SameHostRedirects(HTTPRedirectHandler):
    """Follow https redirects, but never carry credentials to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != "https":
            return None
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and target.netloc.lower() != urlparse(req.full_url).netloc.lower():
            new.remove_header("Authorization")
        return new


def _default_opener(timeout: float) -> Callable[[Request], Any]:
    director = build_opener(_SameHostRedirects)
    return lambda request: director.open(request, timeout=timeout)


class AdoClient:
    def __init__(
        self,
        api_version: str = "7.1",
        opener: Any = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.org_url = os.environ.get("ADO_ORG_URL", "").rstrip("/")
        self.pat = os.environ.get("ADO_PAT", "")
        self.api_version = api_version
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.opener = opener or _default_opener(timeout)
        if not self.org_url:
            raise AdoApiError("ADO_ORG_URL must be set for REST mode")
        if not self.org_url.lower().startswith("https://"):
            raise AdoApiError("ADO_ORG_URL must start with https:// (the PAT is never sent over plain http)")
        if not self.pat:
            raise AdoApiError("ADO_PAT must be set for REST mode")

    def __repr__(self) -> str:
        return f"AdoClient(org_url={self.org_url!r}, api_version={self.api_version!r})"

    @staticmethod
    def _retry_delay(headers: Any, attempt: int) -> float:
        try:
            delay = float(headers.get("Retry-After")) if headers else 2.0**attempt
        except (TypeError, ValueError):
            delay = 2.0**attempt
        return max(0.0, min(delay, MAX_RETRY_DELAY))

    @staticmethod
    def _server_message(exc: HTTPError) -> str:
        try:
            data = json.loads(exc.read())
        except (OSError, ValueError):
            return ""
        message = data.get("message") if isinstance(data, dict) else None
        return message.strip() if isinstance(message, str) else ""

    def _request(
        self,
        path: str,
        query: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        body: Any = None,
        accept: str = "application/json",
    ) -> tuple[bytes, Any]:
        params = dict(query or {})
        params.setdefault("api-version", self.api_version)
        url = f"{self.org_url}/{path.lstrip('/')}?{urlencode(params)}"
        token = base64.b64encode(f":{self.pat}".encode()).decode("ascii")
        headers = {
            "Authorization": f"Basic {token}",
            "Accept": accept,
            "User-Agent": f"ado-pipeline-graph/{_VERSION}",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            request = Request(url, data=data, headers=headers, method=method)
            try:
                with self.opener(request) as response:
                    content = response.read()
                    resp_headers = response.headers
                    status = getattr(response, "status", 200)
                    ctype = resp_headers.get("Content-Type", "") or ""
                if status == 203 or "text/html" in ctype.lower() or content.lstrip()[:1] == b"<":
                    raise AdoApiError(
                        "Azure DevOps returned a sign-in page; authentication failed, "
                        "check that ADO_PAT is valid, unexpired and has the required scope"
                    )
                return content, resp_headers
            except HTTPError as exc:
                if exc.code in RETRY_STATUSES and attempt < self.max_retries:
                    self.sleep(self._retry_delay(exc.headers, attempt))
                    attempt += 1
                    continue
                detail = self._server_message(exc)
                text = _STATUS_MESSAGES.get(exc.code, "service error")
                raise AdoApiError(
                    f"Azure DevOps REST request failed ({exc.code}): {text}" + (f": {detail}" if detail else "")
                ) from None
            except URLError as exc:
                raise AdoApiError(f"could not reach Azure DevOps: {exc.reason}") from None
            except TimeoutError:
                raise AdoApiError(f"request to Azure DevOps timed out after {self.timeout:g}s") from None
            except OSError as exc:
                raise AdoApiError(f"could not reach Azure DevOps: {exc}") from None

    @staticmethod
    def _parse(body: bytes) -> dict[str, Any]:
        try:
            result = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AdoApiError("Azure DevOps returned an invalid JSON response") from None
        if not isinstance(result, dict):
            raise AdoApiError("Azure DevOps returned an unexpected response")
        return result

    def _json(self, path: str, query: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        body, _ = self._request(path, query, **kwargs)
        return self._parse(body)

    def _list(self, path: str, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """GET a list endpoint, following the x-ms-continuationtoken header until exhausted."""
        params = dict(query or {})
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        while True:
            body, headers = self._request(path, params)
            items += [i for i in self._parse(body).get("value", []) if isinstance(i, dict)]
            token = headers.get("x-ms-continuationtoken")
            if not token or token in seen:
                return items
            seen.add(token)
            params["continuationToken"] = token

    @staticmethod
    def _project_path(project: str) -> str:
        return quote(project, safe="")

    def get_pipeline_yaml(self, project: str, pipeline_id: int, branch: str | None = None) -> str:
        project_path = self._project_path(project)
        pipeline = self._json(f"{project_path}/_apis/pipelines/{pipeline_id}")
        configuration = pipeline.get("configuration", {})
        repository = configuration.get("repository", {}) if isinstance(configuration, dict) else {}
        repository_id = repository.get("id") if isinstance(repository, dict) else None
        yaml_path = configuration.get("path") if isinstance(configuration, dict) else None
        if not repository_id or not yaml_path:
            raise AdoApiError("pipeline definition does not expose a YAML repository and path")
        query: dict[str, Any] = {"path": yaml_path, "includeContent": "true"}
        selected_branch = branch or repository.get("defaultBranch")
        if selected_branch:
            query["versionDescriptor.version"] = str(selected_branch).removeprefix("refs/heads/")
            query["versionDescriptor.versionType"] = "branch"
        # Docs: text/plain returns the raw file; application/json returns a GitItem with `content`
        # (only populated when includeContent=true). Ask for text, accept either.
        body, headers = self._request(
            f"{project_path}/_apis/git/repositories/{quote(str(repository_id), safe='')}/items",
            query,
            accept="text/plain",
        )
        if "json" in (headers.get("Content-Type", "") or "").lower():
            try:
                item: Any = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                item = None
            if isinstance(item, dict) and isinstance(item.get("value"), list) and item["value"]:
                item = item["value"][0]
            if isinstance(item, dict) and ("objectId" in item or "gitObjectType" in item or "content" in item):
                content = item.get("content")
                if not isinstance(content, str):
                    raise AdoApiError("Git item response did not include file content")
                return content
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            raise AdoApiError("pipeline YAML response was not valid UTF-8") from None

    def get_expanded_yaml(self, project: str, pipeline_id: int, branch: str | None = None) -> str:
        """Fully template-expanded YAML via the Pipelines Preview API (a dry run; queues no build)."""
        request_body: dict[str, Any] = {"previewRun": True}
        if branch:
            ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
            request_body["resources"] = {"repositories": {"self": {"refName": ref}}}
        result = self._json(
            f"{self._project_path(project)}/_apis/pipelines/{pipeline_id}/preview",
            method="POST",
            body=request_body,
        )
        final_yaml = result.get("finalYaml")
        if not isinstance(final_yaml, str):
            raise AdoApiError("preview response did not include finalYaml")
        return final_yaml

    def get_runs(self, project: str, pipeline_id: int, top: int = 20) -> list[dict[str, Any]]:
        result = self._json(
            f"{self._project_path(project)}/_apis/build/builds",
            {"definitions": pipeline_id, "$top": top},
        )
        return [item for item in result.get("value", []) if isinstance(item, dict)]

    def get_variable_groups(self, project: str) -> list[dict[str, Any]]:
        # Docs list this endpoint as GA in 7.1; no preview suffix needed.
        return self._list(f"{self._project_path(project)}/_apis/distributedtask/variablegroups")

    def get_build_definitions(self, project: str) -> list[dict[str, Any]]:
        return self._list(
            f"{self._project_path(project)}/_apis/build/definitions",
            {"$top": 1000, "includeAllProperties": "true"},
        )
