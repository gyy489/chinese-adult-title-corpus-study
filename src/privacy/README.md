# Privacy remediation and release gates

This stage publishes two deterministic mechanisms:

- strict exact-span replacement with overlap and source-match checks, followed
  by mandatory final-text deduplication; and
- a fail-closed scanner for common direct locators such as email addresses,
  URLs, domains, telephone numbers, identification numbers, and handles.

In the final restricted corpus, researcher review confirmed 111 titles. One
hundred received 132 exact-span replacements, 11 catalog-number cases were
retained, none were excluded, and remediation created two additional duplicate
contributions that were removed. The postbuild direct-locator gate reported
zero matches across 38,298 final texts.

These checks reduce disclosure risk; they are not complete anonymity or an
independent anonymity certification. The literal pre-remediation evidence,
private lexicons, reviewed title strings, per-title decisions, and hashes are
not released.
