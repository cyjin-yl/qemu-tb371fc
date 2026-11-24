# First-stage storage declaration map

The two original coordination files are preserved byte for byte. They contain
only selected fields from the already sanitized fstab, GPT and LP metadata
reports, source paths and hashes, and explicit qualification limits. No raw
firmware, boot arguments, key contents, unique GUID values or userdata payload
is included. Local source paths identify the original observations; they are
not instructions to access another workspace.

The JSON SHA-256 is
`6155e3fbd284c6be57f7e9f4e0841b4265cfc9cbaef8b119d75ae579200c9643`,
which is the input bound by the later vbmeta declaration comparison. The five
LP mount mappings are metadata matches across observations made on different
days, not a coherent snapshot or authenticated Android mount. The earlier
map leaves vendor/odm AVB ownership unknown; the later vbmeta reports establish
only their declared descriptor owners, still without authenticating signatures
or partition contents.
