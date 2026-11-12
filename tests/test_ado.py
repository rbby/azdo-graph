import io
import json
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest

from ado_pipeline_graph.ado import AdoApiError, AdoClient, _SameHostRedirects

PIPELINE = {
    "configuration": {
        "path": "/azure-pipelines.yml",
        "repository": {"id": "repo-1", "defaultBranch": "refs/heads/main"},
    }
}


def http_error(code, body=b"", headers=None):
    msg = Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return HTTPError("https://dev.azure.com/org/x", code, "err", msg, io.BytesIO(body))


def test_authenticates_and_fetches_runs(make_client, Response):
    client, seen, _ = make_client(Response({"value": [{"id": 5}]}))
    assert client.get_runs("Project A", 17, 3) == [{"id": 5}]
    request = seen[0]
    assert request.get_header("Authorization").startswith("Basic ")
    assert request.get_header("User-agent").startswith("ado-pipeline-graph/")
    assert "Project%20A" in request.full_url
    assert "secret" not in request.full_url
    assert "secret" not in repr(client)


def test_requires_environment_configuration(monkeypatch):
    monkeypatch.delenv("ADO_ORG_URL", raising=False)
    monkeypatch.delenv("ADO_PAT", raising=False)
    with pytest.raises(AdoApiError, match="ADO_ORG_URL"):
        AdoClient()


def test_rejects_non_https_org_url(monkeypatch):
    monkeypatch.setenv("ADO_ORG_URL", "http://dev.azure.com/org")
    monkeypatch.setenv("ADO_PAT", "secret")
    with pytest.raises(AdoApiError, match="https://"):
        AdoClient()


@pytest.mark.parametrize("code,text", [(401, "ADO_PAT"), (403, "access denied"), (404, "not found")])
def test_http_errors_include_server_message(code, text, make_client):
    body = json.dumps({"message": "TF400813: nope"}).encode()
    client, _, sleeps = make_client(http_error(code, body))
    with pytest.raises(AdoApiError, match=f"{code}.*{text}.*TF400813: nope"):
        client.get_runs("P", 1)
    assert sleeps == []


def test_error_without_json_body(make_client):
    client, _, _ = make_client(http_error(404, b"<html>nope</html>"))
    with pytest.raises(AdoApiError, match="404.*not found") as info:
        client.get_runs("P", 1)
    assert "html" not in str(info.value)


def test_sign_in_page_is_authentication_failure(make_client, Response):
    page = Response(b"<html>Sign in</html>", {"Content-Type": "text/html"}, status=203)
    client, _, _ = make_client(page)
    with pytest.raises(AdoApiError, match="authentication failed"):
        client.get_runs("P", 1)


def test_html_body_without_203_is_authentication_failure(make_client, Response):
    client, _, _ = make_client(Response(b"<html></html>", {"Content-Type": "text/html"}))
    with pytest.raises(AdoApiError, match="authentication failed"):
        client.get_runs("P", 1)


def test_429_retries_honouring_retry_after(make_client, Response):
    client, requests, sleeps = make_client(
        http_error(429, headers={"Retry-After": "7"}), Response({"value": [{"id": 1}]})
    )
    assert client.get_runs("P", 1) == [{"id": 1}]
    assert sleeps == [7.0]
    assert len(requests) == 2


def test_5xx_retry_exhaustion(make_client):
    body = json.dumps({"message": "overloaded"}).encode()
    client, requests, sleeps = make_client(*[http_error(503, body) for _ in range(4)])
    with pytest.raises(AdoApiError, match="503.*overloaded"):
        client.get_runs("P", 1)
    assert len(requests) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_timeout_and_os_errors(make_client):
    client, _, _ = make_client(TimeoutError())
    with pytest.raises(AdoApiError, match="timed out after 30s"):
        client.get_runs("P", 1)
    client, _, _ = make_client(ConnectionResetError("reset"), timeout=5)
    with pytest.raises(AdoApiError, match="reset"):
        client.get_runs("P", 1)


def test_redirect_to_other_host_drops_credentials():
    handler = _SameHostRedirects()
    request = Request("https://dev.azure.com/org/x", headers={"Authorization": "Basic abc"})

    def follow(url):
        return handler.redirect_request(request, io.BytesIO(), 302, "Found", Message(), url)

    assert follow("https://evil.example.com/x").get_header("Authorization") is None
    assert follow("https://dev.azure.com/org/y").get_header("Authorization") == "Basic abc"
    assert follow("http://dev.azure.com/org/y") is None


def test_pagination_follows_continuation_token(make_client, Response):
    client, requests, _ = make_client(
        Response({"value": [{"id": 1}]}, {"x-ms-continuationtoken": "tok2"}),
        Response({"value": [{"id": 2}]}),
    )
    assert client.get_build_definitions("P") == [{"id": 1}, {"id": 2}]
    assert "includeAllProperties=true" in requests[0].full_url
    assert "continuationToken" not in requests[0].full_url
    assert "continuationToken=tok2" in requests[1].full_url


def test_variable_groups_use_configured_api_version(make_client, Response):
    client, requests, _ = make_client(Response({"value": [{"name": "Shared", "id": 13}]}))
    assert client.get_variable_groups("P") == [{"name": "Shared", "id": 13}]
    assert "api-version=7.1" in requests[0].full_url
    assert "preview" not in requests[0].full_url


def test_pipeline_yaml_text_response(make_client, Response):
    client, requests, _ = make_client(Response(PIPELINE), Response(b"trigger: none\n", {"Content-Type": "text/plain"}))
    assert client.get_pipeline_yaml("Project", 4) == "trigger: none\n"
    assert requests[1].get_header("Accept") == "text/plain"
    assert "_apis/git/repositories/repo-1/items" in requests[1].full_url
    assert "includeContent=true" in requests[1].full_url
    assert "versionDescriptor.version=main" in requests[1].full_url


def test_pipeline_yaml_json_item_response(make_client, Response):
    item = {
        "objectId": "61a8",
        "gitObjectType": "blob",
        "commitId": "23d0",
        "path": "/azure-pipelines.yml",
        "content": "trigger: none\n",
    }
    client, _, _ = make_client(Response(PIPELINE), Response(item))
    assert client.get_pipeline_yaml("Project", 4) == "trigger: none\n"
    client, _, _ = make_client(Response(PIPELINE), Response({"count": 1, "value": [item]}))
    assert client.get_pipeline_yaml("Project", 4) == "trigger: none\n"


def test_pipeline_yaml_json_item_without_content(make_client, Response):
    item = {"objectId": "61a8", "gitObjectType": "blob", "path": "/azure-pipelines.yml"}
    client, _, _ = make_client(Response(PIPELINE), Response(item))
    with pytest.raises(AdoApiError, match="content"):
        client.get_pipeline_yaml("Project", 4)


def test_pipeline_without_yaml_configuration(make_client, Response):
    client, _, _ = make_client(Response({"configuration": {}}))
    with pytest.raises(AdoApiError, match="YAML repository"):
        client.get_pipeline_yaml("Project", 4)


def test_expanded_yaml_preview(make_client, Response):
    client, requests, _ = make_client(Response({"finalYaml": "stages: []\n"}), Response({"finalYaml": "x: 1\n"}))
    assert client.get_expanded_yaml("Project A", 9) == "stages: []\n"
    request = requests[0]
    assert request.get_method() == "POST"
    assert "Project%20A/_apis/pipelines/9/preview" in request.full_url
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"previewRun": True}

    client.get_expanded_yaml("Project A", 9, branch="dev")
    assert json.loads(requests[1].data) == {
        "previewRun": True,
        "resources": {"repositories": {"self": {"refName": "refs/heads/dev"}}},
    }


def test_expanded_yaml_missing_final_yaml(make_client, Response):
    client, _, _ = make_client(Response({}))
    with pytest.raises(AdoApiError, match="finalYaml"):
        client.get_expanded_yaml("P", 1)


def test_missing_pat(monkeypatch):
    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/org")
    monkeypatch.delenv("ADO_PAT", raising=False)
    with pytest.raises(AdoApiError, match="ADO_PAT"):
        AdoClient()


def test_unparseable_retry_after_falls_back_to_backoff(make_client, Response):
    client, _, sleeps = make_client(http_error(429, headers={"Retry-After": "soon"}), Response({"value": []}))
    assert client.get_runs("P", 1) == []
    assert sleeps == [1.0]


def test_retry_after_is_capped(make_client, Response):
    client, _, sleeps = make_client(http_error(429, headers={"Retry-After": "99999"}), Response({"value": []}))
    client.get_runs("P", 1)
    assert 0 < sleeps[0] <= 300


def test_unmapped_status_is_service_error(make_client):
    client, _, _ = make_client(http_error(418))
    with pytest.raises(AdoApiError, match=r"\(418\).*service error"):
        client.get_runs("P", 1)


def test_url_error_is_reported(make_client):
    from urllib.error import URLError

    client, _, _ = make_client(URLError("dns down"))
    with pytest.raises(AdoApiError, match="could not reach Azure DevOps: dns down"):
        client.get_runs("P", 1)


@pytest.mark.parametrize(
    "body,match",
    [
        (b"{not json", "invalid JSON"),
        (b"\xff\xfe", "invalid JSON"),
        (b"[1, 2]", "unexpected response"),
    ],
)
def test_bad_json_bodies(make_client, Response, body, match):
    client, _, _ = make_client(Response(body))
    with pytest.raises(AdoApiError, match=match):
        client.get_runs("P", 1)


def test_non_utf8_yaml_file(make_client, Response):
    client, _, _ = make_client(Response(PIPELINE), Response(b"\xff\xfe", {"Content-Type": "text/plain"}))
    with pytest.raises(AdoApiError, match="UTF-8"):
        client.get_pipeline_yaml("P", 4)


def _version(request):
    return parse_qs(urlparse(request.full_url).query)["versionDescriptor.version"]


def test_branch_override_and_prefix_stripping(make_client, Response):
    text = {"Content-Type": "text/plain"}
    client, requests, _ = make_client(
        Response(PIPELINE),
        Response(b"a: 1", text),
        Response(PIPELINE),
        Response(b"a: 1", text),
        Response({"configuration": {"path": "/p.yml", "repository": {"id": "r"}}}),
        Response(b"a: 1", text),
    )
    client.get_pipeline_yaml("P", 4, branch="refs/heads/feature/x")
    assert _version(requests[1]) == ["feature/x"]
    client.get_pipeline_yaml("P", 4, branch="dev")
    assert _version(requests[3]) == ["dev"]
    client.get_pipeline_yaml("P", 4)  # no branch and no defaultBranch: no version filter
    assert "versionDescriptor" not in requests[5].full_url
