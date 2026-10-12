# Security policy

## Supported versions

Security fixes are made for the latest released version only. Please upgrade before reporting
(`uvx aws-blog-mcp@latest` always runs the newest release).

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/anhyobin/aws-blog-mcp/security/advisories/new)
(the **Report a vulnerability** button on the repository's **Security** tab). Include:

- the version (`uvx aws-blog-mcp@latest --version`) and your MCP client,
- the tool call or input that triggers the problem,
- what happens and what impact you think it has,
- a proof of concept, if you have one. Please link to the blog post involved instead of pasting
  its content.

This is a personal open source project maintained in spare time. You can expect an
acknowledgement within a week; fixes are released as soon as practical, and reporters are
credited in the advisory unless they prefer otherwise.

## Scope

This server is a local, read-only client of `https://aws.amazon.com`. In scope are weaknesses in
this project's code and packaging, for example:

- **Prompt injection through fetched content.** Post bodies, titles, excerpts and author names
  come from a third-party website and are passed to the model. The server labels them as
  reference material (the `get_post` header and the server instructions tell the model not to
  follow instructions found in posts), but it cannot guarantee that a model will comply. Reports
  of ways to make this labelling ineffective, or of output that could be mistaken for tool or
  system messages, are welcome.
- **Server-side request forgery (SSRF).** The HTTP client only fetches `https://aws.amazon.com`
  URLs: user-supplied URLs are validated and normalized, and every redirect target is checked
  again (at most 3 redirects). Any way to make the server request another host, scheme or port is
  in scope.
- **Resource exhaustion.** Response bodies are size-capped, retries and `Retry-After` waits are
  bounded, and caches are bounded. Inputs that make the server use unbounded memory or CPU, or
  hang, are in scope.
- **Unsafe output.** Markdown produced from post HTML that could execute script or exfiltrate
  data in a client that renders it (for example through crafted links or image URLs).
- **Supply chain.** Problems in the published package, the release workflow or the MCP Registry
  entry.

Out of scope:

- Vulnerabilities in aws.amazon.com or other AWS services. Report those to AWS through
  [AWS Vulnerability Reporting](https://aws.amazon.com/security/vulnerability-reporting/).
- The accuracy or safety of blog content itself.
- Vulnerabilities in MCP clients, or in dependencies that do not affect this project (report them
  upstream; a heads-up is still appreciated if this project should pin or upgrade something).
- The AWS directory API or page template changing in a way that breaks the server. Please open a
  regular issue for that.
