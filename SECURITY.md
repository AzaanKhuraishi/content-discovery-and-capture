# Security Policy and Trust Boundaries

Content Discovery and Capture is a public open-source repository. This policy describes how to report a vulnerability and the limits of the protections provided by the application. It does not claim that the application is secure against every threat.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting form from the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue for a suspected vulnerability. If private reporting is unavailable in your GitHub account, contact the maintainer privately through [Azaan Khuraishi's GitHub profile](https://github.com/AzaanKhuraishi) and request a private reporting channel.

Include the affected commit or version, a concise description, reproduction steps that use synthetic data where possible, and the impact. Do not include passwords, tokens, cookies, signed URLs, private source material or other secrets in a report. If you accidentally disclose a secret, revoke it immediately and notify the maintainer through the same private channel.

The maintainer aims to acknowledge reports within seven days, investigate privately, and coordinate a fix or mitigation before publishing details. Please allow reasonable time for triage and disclosure coordination.

Source content is untrusted data. The engine does not execute commands found in source pages or documents. It accepts only typed operations and checks registered scopes and reviewed selections before source I/O. Macros and source scripts are not executed by the document converters. The optional browser necessarily executes page JavaScript within its browser runtime.

The user review panel listens on loopback and validates Host, Origin and a one-use form nonce. It HTML-escapes source titles. No model-facing tool grants approval. A local process or agent with unrestricted access to the same OS account can modify files or operate the user's browser; this workflow gate does not claim to defeat that adversary. Host integrations must preserve a real user decision and must not automate the approval panel.

Source identity excludes known credential parameters, URL user-info and browser session state. Transient retrieval URLs stay in memory. Persisted observations, event logs and exported provenance use safe locators. Arbitrary secrets cannot be detected perfectly from text alone; do not supply credentials as titles, descriptions or tool arguments. Captured original bytes can contain sensitive content and remain private. Text exports with recognised credential-like content are blocked rather than silently rewriting originals.

Web navigation, dependency origins, recursion, bytes, actions and time are bounded. Private-network access is disabled unless included in the source scope and review. Redirects are checked. The standard HTTP provider checks resolved addresses before connecting; it is not a hardened multi-tenant SSRF proxy and does not provide DNS-pinning guarantees. Use trusted source domains and a network-restricted runtime for hostile multi-tenant workloads.

Filesystem capture verifies the reviewed file identity/metadata, uses no-follow on the final file open, checks scope and checks for changes during copying. Symlinks are not traversed during discovery. It is designed for user-controlled collections, not adversarial concurrent filesystem namespace mutation. Originals are copied, never hard-linked to mutable input files. OS access timestamps can still change on reads.

Conversion runs in a child process with a timeout and per-file output-size limit where supported. Optional parsers and media libraries remain dependencies that should be kept current. A parsing process is not a complete OS security sandbox. Archive validators reject excessive expansion sizes and never extract archive members into source directories.

Browser providers can have limits they cannot enforce before transfer (for example buffered browser downloads and page runtime network traffic). These limitations must be disclosed by the provider; a job requiring strict transfer ceilings should use the bounded HTTP provider or a runtime with stronger controls. Browser security warnings, client blocks, CAPTCHAs and source access controls are not bypassed.

The repository includes automated dependency review, dependency update proposals and code scanning. GitHub secret scanning and push protection are repository/account features whose availability is controlled by GitHub. These services can miss vulnerabilities and do not replace review of changes or safe handling of captured content.

The application retains the following residual risks: a local process or agent with unrestricted access to the same OS account can modify files or operate the user's browser; opening a repository with automatic Codex setup enabled runs its checked-in bootstrap code; the HTTP provider is not a hardened multi-tenant SSRF proxy and does not provide DNS-pinning guarantees; filesystem checks do not defeat every adversarial concurrent namespace mutation; browser runtimes may access more network state than the provider can observe; and secret detection from arbitrary content is incomplete. Use trusted repositories, trusted sources and a network-restricted runtime for hostile workloads.
