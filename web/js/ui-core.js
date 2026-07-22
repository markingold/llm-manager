export function $(id) {
  return document.getElementById(id);
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function setStatus(msg, tone = "info") {
  const box = $("statusBox");
  box.textContent = msg;
  box.className = `status ${tone}`;
}

export function setOut(obj) {
  $("out").textContent = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
}

export function setBudgetBanner(message, tone = "warn") {
  const banner = $("budgetBanner");
  if (!banner) return;
  if (!message) {
    banner.style.display = "none";
    banner.textContent = "";
    banner.className = "status";
    return;
  }
  banner.style.display = "";
  banner.className = `status ${tone}`;
  banner.textContent = message;
}

export function basename(p) {
  if (!p || typeof p !== "string") return "";
  const s = p.replace(/\/+$/, "");
  return s.split("/").pop() || "";
}

export function engineTone(listening, activeState) {
  if (activeState === "active" && listening) return "ok";
  if (activeState === "inactive" && !listening) return "warn";
  return "bad";
}

export function engineLabel(listening, activeState) {
  if (activeState === "active" && listening) return { tone: "ok", text: "RUNNING + LISTENING" };
  if (activeState === "active" && !listening) return { tone: "warn", text: "RUNNING (NOT LISTENING)" };
  if (activeState === "inactive") return { tone: "warn", text: "STOPPED" };
  return { tone: "bad", text: "UNKNOWN" };
}

export function fmtEngineMeta(e) {
  const u = e.unit || "(no unit)";
  const st = e.systemd?.ActiveState || e.systemd?.error || "unknown";
  const sub = e.systemd?.SubState ? `/${e.systemd.SubState}` : "";
  const pid = e.systemd?.MainPID ? ` pid=${e.systemd.MainPID}` : "";
  const listen = e.listening ? "listening" : "not-listening";
  return `${u} | ${st}${sub}${pid} | ${listen} | ${e.base}\n${e.active}`;
}

export function setSelectOptions(selectEl, items, activeName) {
  if (!selectEl) return;
  const prev = selectEl.value;
  selectEl.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "";
  opt0.textContent = items.length ? "- choose a model -" : "(no models found)";
  selectEl.appendChild(opt0);

  for (const name of items) {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name;
    selectEl.appendChild(o);
  }

  const trySet = (v) => {
    if (!v) return false;
    const ok = [...selectEl.options].some(o => o.value === v);
    if (ok) { selectEl.value = v; return true; }
    return false;
  };

  if (trySet(activeName)) return;
  if (trySet(prev)) return;
  selectEl.value = "";
}

export function setEngineButtonStates(prefix, activeState) {
  const isActive = activeState === "active";
  const startBtn = $(`${prefix}Start`);
  const stopBtn = $(`${prefix}Stop`);
  const rstBtn = $(`${prefix}Restart`);

  if (startBtn) startBtn.disabled = isActive;
  if (stopBtn) stopBtn.disabled = !isActive;
  if (rstBtn) rstBtn.disabled = !isActive;

  if (startBtn) startBtn.textContent = isActive ? "Start (running)" : "Start";
  if (stopBtn) stopBtn.textContent = isActive ? "Stop" : "Stop (stopped)";
  if (rstBtn) rstBtn.textContent = isActive ? "Restart" : "Restart (stopped)";
}
