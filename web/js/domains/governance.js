import { api } from "../api.js";
import { $, setOut } from "../ui-core.js";

const DOCS = {
  models: null,
  policies: null,
};

const VERSIONS = {
  models: "",
  policies: "",
};

function selectedResource() {
  return $("govResource")?.value || "models";
}

function setVersionsUI() {
  if ($("govModelsVersion")) {
    $("govModelsVersion").textContent = `models version: ${VERSIONS.models || "-"}`;
  }
  if ($("govPoliciesVersion")) {
    $("govPoliciesVersion").textContent = `policies version: ${VERSIONS.policies || "-"}`;
  }
}

function setEditorFromResource() {
  const res = selectedResource();
  if ($("govEditor")) {
    $("govEditor").value = JSON.stringify(DOCS[res] || {}, null, 2);
  }
}

function parseEditorDoc() {
  const raw = $("govEditor")?.value?.trim() || "";
  if (!raw) throw new Error("governance editor is empty");
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object") throw new Error("governance document must be a JSON object");
  return parsed;
}

function renderAudit(rows) {
  const body = $("govAuditBody");
  if (!body) return;
  body.innerHTML = "";

  const entries = Array.isArray(rows) ? [...rows].reverse().slice(0, 120) : [];
  if (!entries.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="7" class="muted">No governance audit entries.</td>';
    body.appendChild(tr);
    return;
  }

  for (const e of entries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${e.ts || "-"}</td>
      <td>${e.resource || "-"}</td>
      <td>${e.action || "-"}</td>
      <td>${e.actor || "-"}</td>
      <td>${e.outcome || "-"}</td>
      <td>${e.reason || "-"}</td>
      <td class="mono">${e.before_version || "-"} -> ${e.after_version || "-"}</td>
    `;
    body.appendChild(tr);
  }
}

export async function refreshGovernancePanel() {
  try {
    const [modelsRes, policiesRes, stateRes] = await Promise.all([
      api("/providers/models"),
      api("/providers/policies"),
      api("/providers/state"),
    ]);

    DOCS.models = modelsRes?.models || {};
    DOCS.policies = policiesRes?.policies || {};
    VERSIONS.models = modelsRes?.version || "";
    VERSIONS.policies = policiesRes?.version || "";

    setVersionsUI();
    setEditorFromResource();

    const state = stateRes?.state || {};
    renderAudit(state?.governance_audit || []);
  } catch (e) {
    setOut({ error: e.message, action: "governance_refresh" });
  }
}

async function governanceWrite(validateOnly) {
  const resource = selectedResource();
  const reason = $("govReason")?.value?.trim() || "";
  const actor = $("govActor")?.value?.trim() || "webui";
  const expectedVersion = VERSIONS[resource] || "";

  if (!validateOnly && !reason) {
    setOut({ error: "reason is required for apply" });
    return;
  }

  if (!validateOnly && $("govRequireConfirm")?.checked) {
    const ok = window.confirm(`Apply ${resource} governance changes?`);
    if (!ok) return;
  }

  try {
    const document = parseEditorDoc();
    setOut({ running: true, action: validateOnly ? "governance_validate" : "governance_apply", resource });
    const result = await api(`/providers/${resource}`, {
      method: "PUT",
      body: {
        document,
        expected_version: expectedVersion || null,
        validate_only: !!validateOnly,
        reason,
        actor,
      },
    });
    setOut(result);
    await refreshGovernancePanel();
  } catch (e) {
    setOut({ error: e.message, action: validateOnly ? "governance_validate" : "governance_apply", resource });
  }
}

async function governanceRollback() {
  const resource = selectedResource();
  const reason = $("govReason")?.value?.trim() || "";
  const actor = $("govActor")?.value?.trim() || "webui";
  const expectedVersion = VERSIONS[resource] || "";

  if (!reason) {
    setOut({ error: "reason is required for rollback" });
    return;
  }

  if ($("govRequireConfirm")?.checked) {
    const ok = window.confirm(`Rollback ${resource} to last good snapshot?`);
    if (!ok) return;
  }

  try {
    setOut({ running: true, action: "governance_rollback", resource });
    const result = await api(`/providers/${resource}/rollback`, {
      method: "POST",
      body: {
        expected_version: expectedVersion || null,
        reason,
        actor,
      },
    });
    setOut(result);
    await refreshGovernancePanel();
  } catch (e) {
    setOut({ error: e.message, action: "governance_rollback", resource });
  }
}

async function applyProviderFlags() {
  const modelKey = $("govFlagModelKey")?.value?.trim() || "";
  const reason = $("govReason")?.value?.trim() || "";
  const actor = $("govActor")?.value?.trim() || "webui";
  if (!modelKey) {
    setOut({ error: "provider:model_id model key is required" });
    return;
  }
  if (!reason) {
    setOut({ error: "reason is required for flags update" });
    return;
  }

  if ($("govRequireConfirm")?.checked) {
    const ok = window.confirm(`Apply provider-model flags for ${modelKey}?`);
    if (!ok) return;
  }

  try {
    setOut({ running: true, action: "governance_flags_update", model_key: modelKey });
    const result = await api("/providers/state/provider-model-flags", {
      method: "POST",
      body: {
        model_key: modelKey,
        disabled_until_manual_review: !!$("govFlagDisabled")?.checked,
        exclude_from_free_rotation: !!$("govFlagExclude")?.checked,
        reason,
        actor,
      },
    });
    setOut(result);
    await refreshGovernancePanel();
  } catch (e) {
    setOut({ error: e.message, action: "governance_flags_update", model_key: modelKey });
  }
}

export function wireGovernanceDomain() {
  $("govResource")?.addEventListener("change", setEditorFromResource);
  $("btnGovRefresh")?.addEventListener("click", refreshGovernancePanel);
  $("btnGovValidate")?.addEventListener("click", () => governanceWrite(true));
  $("btnGovApply")?.addEventListener("click", () => governanceWrite(false));
  $("btnGovRollback")?.addEventListener("click", governanceRollback);
  $("btnGovApplyFlags")?.addEventListener("click", applyProviderFlags);

  refreshGovernancePanel();
}
