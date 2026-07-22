import test from "node:test";
import assert from "node:assert/strict";

import { escapeHtml } from "../../web/js/ui-core.js";

test("escapeHtml neutralizes stored dashboard markup and quoted attributes", () => {
  const payload = `<img src=x onerror="globalThis.pwned=true">'&`;
  const escaped = escapeHtml(payload);
  assert.equal(escaped, "&lt;img src=x onerror=&quot;globalThis.pwned=true&quot;&gt;&#39;&amp;");
  assert.equal(escaped.includes("<img"), false);
  assert.equal(escaped.includes('"'), false);
});
