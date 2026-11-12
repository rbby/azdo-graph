from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class AdoApiError(RuntimeError):
    """An Azure DevOps Services API request failed."""


class AdoClient:
    def __init__(
        self,
        api_version: str = "7.1",
        opener: Any = urlopen,
    ) -> None:
        self.org_url = os.environ.get("ADO_ORG_URL", "").rstrip("/")
        self.pat = os.environ.get("ADO_PAT", "")
        self.api_version = api_version
        self.opener = opener
        if not self.org_url:
            raise AdoApiError("ADO_ORG_URL must be set for REST mode")
        if not self.pat:
            raise AdoApiError("ADO_PAT must be set for REST mode")

    def _get(self, path: str, query: dict[str, Any] | None = None) -> tuple[bytes, str]:
        params = dict(query or {})
        params.setdefault("api-version", self.api_version)
        url = f"{self.org_url}/{path.lstrip('/')}?{urlencode(params)}"
        token = base64.b64encode(f":{self.pat}".encode()).decode("ascii")
        request = Request(url, headers={"Authorization": f"Basic {token}", "Accept": "application/json"})
        try:
            with self.opener(request) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
                return body, content_type
        except HTTPError as exc:
            messages = {
                401: "authentication failed; check ADO_PAT",
                403: "access denied; check project and pipeline permissions",
                404: "the requested Azure DevOps resource was not found",
            }
            raise AdoApiError(f"Azure DevOps REST request failed ({exc.code}): "
                              f"{messages.get(exc.code, 'service error')}") from None
        except URLError as exc:
            raise AdoApiError(f"could not reach Azure DevOps: {exc.reason}") from None

    def _json(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        body, _ = self._get(path, query)
        try:
            result = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AdoApiError("Azure DevOps returned an invalid JSON response") from None
        if not isinstance(result, dict):
            raise AdoApiError("Azure DevOps returned an unexpected response")
        return result

    @staticmethod
    def _project_path(project: str) -> str:
        return quote(project, safe="")

    def get_pipeline_yaml(self, project: str, pipeline_id: int, branch: str | None = None) -> str:
        project_path = self._project_path(project)
        pipeline = self._json(
            f"{project_path}/_apis/pipelines/{pipeline_id}",
        )
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
        body, _ = self._get(
            f"{project_path}/_apis/git/repositories/{quote(str(repository_id), safe='')}/items",
            query,
        )
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            raise AdoApiError("pipeline YAML response was not valid UTF-8") from None

    def get_runs(self, project: str, pipeline_id: int, top: int = 20) -> list[dict[str, Any]]:
        result = self._json(
            f"{self._project_path(project)}/_apis/build/builds",
            {"definitions": pipeline_id, "$top": top},
        )
        return [item for item in result.get("value", []) if isinstance(item, dict)]

    def get_variable_groups(self, project: str) -> list[dict[str, Any]]:
        result = self._json(
            f"{self._project_path(project)}/_apis/distributedtask/variablegroups",
            {"api-version": "7.1-preview.2"},
        )
        return [item for item in result.get("value", []) if isinstance(item, dict)]

    def get_build_definitions(self, project: str) -> list[dict[str, Any]]:
        result = self._json(
            f"{self._project_path(project)}/_apis/build/definitions",
            {"$top": 1000},
        )
        return [item for item in result.get("value", []) if isinstance(item, dict)]
