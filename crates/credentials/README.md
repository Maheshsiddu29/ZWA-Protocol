# `zwa-credentials`

Canonical issuer and credential-authority signed-root envelopes, root metadata, and credential/policy value types.

Unsigned payloads have a deterministic byte encoding that is the data-to-be-signed. Signature material is an opaque validated byte container because no root signature algorithm has been approved. Structural and freshness validation produces a `StructurallyValid` marker; it is not cryptographic authentication.

Matcher code must consume these envelopes rather than inventing its own root payload schema.
