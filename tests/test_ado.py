import json

from ado_pipeline_graph.ado import AdoApiError, AdoClient


class Response:
    headers = {"Content-Type": "application/json"}

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.value


def test_client_authenticates_and_fetches_runs(monkeypatch):
    seen = []

    def opener(request):
        seen.append(request)
        return Response(json.dumps({"value": [{"id": 5}]}).encode())

    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/org")
    monkeypatch.setenv("ADO_PAT", "secret")
    client = AdoClient(opener=opener)
    assert client.get_runs("Project A", 17, 3) == [{"id": 5}]
    assert seen[0].get_header("Authorization").startswith("Basic ")
    assert "Project%20A" in seen[0].full_url
    assert "secret" not in seen[0].full_url


def test_client_requires_environment_configuration(monkeypatch):
    monkeypatch.delenv("ADO_ORG_URL", raising=False)
    monkeypatch.delenv("ADO_PAT", raising=False)
    try:
        AdoClient()
    except AdoApiError as exc:
        assert "ADO_ORG_URL" in str(exc)
    else:
        raise AssertionError("client accepted missing environment configuration")


def test_client_fetches_pipeline_yaml_and_variable_groups(monkeypatch):
    responses = [
        {
            "configuration": {
                "path": "/azure-pipelines.yml",
                "repository": {"id": "repo-1", "defaultBranch": "refs/heads/main"},
            }
        },
        "trigger: none\n",
        {"value": [{"name": "Shared", "id": 13}]},
    ]
    requests = []

    def opener(request):
        requests.append(request)
        value = responses.pop(0)
        body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        return Response(body)

    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/org")
    monkeypatch.setenv("ADO_PAT", "secret")
    client = AdoClient(opener=opener)
    assert client.get_pipeline_yaml("Project", 4) == "trigger: none\n"
    assert client.get_variable_groups("Project") == [{"name": "Shared", "id": 13}]
    assert any("_apis/git/repositories/repo-1/items" in request.full_url for request in requests)
    assert any("7.1-preview.2" in request.full_url for request in requests)
