# Security policy

## Reporting a vulnerability

Do not report security vulnerabilities in a public issue.

Use GitHub's private vulnerability reporting for this repository. Open the **Security** tab of https://github.com/rbby/azdo-graph, then choose **Report a vulnerability**. The report stays private until the maintainer publishes an advisory.

Include:

- A description of the problem and its impact.
- Steps to reproduce, with a minimal pipeline file or command where possible.
- The commit or version you tested.

Remove any real personal access token, organisation URL or pipeline content from the report.

## Personal access tokens

ado-pipeline-graph reads its PAT from the `ADO_PAT` environment variable only. These rules apply when you use it:

- Set `ADO_PAT` in your shell or in a secret store. Do not pass the token as a command-line argument, because it can end up in shell history and process listings. The tool has no option that accepts a token.
- Never commit a token, and never put one in a file in a repository, including `.env` files, pipeline YAML, test fixtures or documentation.
- Grant the PAT only the scopes that the commands you run need. The default single-pipeline REST mode needs build scope. If build scope is not allowed, use `--no-expand`.
- Use the shortest practical expiry. If a token may have leaked, revoke it in Azure DevOps and create a new one.
- The tool sends the token only over HTTPS, refuses `ADO_ORG_URL` values that do not start with `https://`, and removes the `Authorization` header on a redirect to another host. It does not print the token.
