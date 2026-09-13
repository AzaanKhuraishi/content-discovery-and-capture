# Security and trust boundaries

Source content is untrusted data. The engine does not execute commands found in source pages or documents. It accepts only typed operations and checks registered scopes and reviewed selections before source I/O. Macros and source scripts are not executed by the document converters. The optional browser necessarily executes page JavaScript within its browser runtime.

The user review panel listens on loopback and validates Host, Origin and a one-use form nonce. It HTML-escapes source titles. No model-facing tool grants approval. A local process or agent with unrestricted access to the same OS account can modify files or operate the user's browser; this workflow gate does not claim to defeat that adversary. Host integrations must preserve a real user decision and must not automate the approval panel.

Source identity excludes known credential parameters, URL user-info and browser session state. Transient retrieval URLs stay in memory. Persisted observations, event logs and exported provenance use safe locators. Arbitrary secrets cannot be detected perfectly from text alone; do not supply credentials as titles, descriptions or tool arguments. Captured original bytes can contain sensitive content and remain private. Text exports with recognised credential-like content are blocked rather than silently rewriting originals.

Web navigation, dependency origins, recursion, bytes, actions and time are bounded. Private-network access is disabled unless included in the source scope and review. Redirects are checked. The standard HTTP provider checks resolved addresses before connecting; it is not a hardened multi-tenant SSRF proxy and does not provide DNS-pinning guarantees. Use trusted source domains and a network-restricted runtime for hostile multi-tenant workloads.

Filesystem capture verifies the reviewed file identity/metadata, uses no-follow on the final file open, checks scope and checks for changes during copying. Symlinks are not traversed during discovery. It is designed for user-controlled collections, not adversarial concurrent filesystem namespace mutation. Originals are copied, never hard-linked to mutable input files. OS access timestamps can still change on reads.

Conversion runs in a child process with a timeout and per-file output-size limit where supported. Optional parsers and media libraries remain dependencies that should be kept current. A parsing process is not a complete OS security sandbox. Archive validators reject excessive expansion sizes and never extract archive members into source directories.

Browser providers can have limits they cannot enforce before transfer (for example buffered browser downloads and page runtime network traffic). These limitations must be disclosed by the provider; a job requiring strict transfer ceilings should use the bounded HTTP provider or a runtime with stronger controls. Browser security warnings, client blocks, CAPTCHAs and source access controls are not bypassed.

Report a security concern privately to the repository maintainer once a repository is published. No repository or reporting endpoint has been created by this local build. Do not include credentials, original private content or signed URLs in public reports.
