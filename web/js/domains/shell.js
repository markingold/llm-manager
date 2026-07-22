import { $ } from "../ui-core.js?v=20260722_1";

const NAV_SECTIONS = ["operations", "router", "evaluation", "providers-budget", "jobs"];
const SURFACES = ["all", "read", "mutate"];

function currentParams() {
  return new URLSearchParams(window.location.search || "");
}

function setUrlParam(key, value) {
  const params = currentParams();
  if (!value) params.delete(key);
  else params.set(key, value);
  const query = params.toString();
  const next = `${window.location.pathname}${query ? `?${query}` : ""}`;
  window.history.replaceState({}, "", next);
}

function setActiveNav(section) {
  for (const name of NAV_SECTIONS) {
    const active = name === section;
    const btn = document.querySelector(`[data-nav-btn="${name}"]`);
    const panel = document.querySelector(`[data-nav-section="${name}"]`);
    if (btn) btn.classList.toggle("active", active);
    if (panel) panel.style.display = active ? "" : "none";
  }
}

function setSurfaceFilter(surface) {
  for (const name of SURFACES) {
    const btn = document.querySelector(`[data-surface-btn="${name}"]`);
    if (btn) btn.classList.toggle("active", name === surface);
  }

  const cards = document.querySelectorAll("[data-surface]");
  for (const card of cards) {
    const cardSurface = card.getAttribute("data-surface") || "read";
    const visible = surface === "all" || cardSurface === surface;
    card.style.display = visible ? "" : "none";
  }
}

export function wireShellDomain() {
  const params = currentParams();
  const initialNav = NAV_SECTIONS.includes(params.get("nav")) ? params.get("nav") : "operations";
  const initialSurface = SURFACES.includes(params.get("surface")) ? params.get("surface") : "all";

  setActiveNav(initialNav);
  setSurfaceFilter(initialSurface);

  for (const section of NAV_SECTIONS) {
    document.querySelector(`[data-nav-btn="${section}"]`)?.addEventListener("click", () => {
      setActiveNav(section);
      setUrlParam("nav", section);
    });
  }

  for (const surface of SURFACES) {
    document.querySelector(`[data-surface-btn="${surface}"]`)?.addEventListener("click", () => {
      setSurfaceFilter(surface);
      setUrlParam("surface", surface === "all" ? "" : surface);
    });
  }

  $("btnJumpEval")?.addEventListener("click", () => {
    setActiveNav("evaluation");
    setUrlParam("nav", "evaluation");
  });

  $("btnJumpRouter")?.addEventListener("click", () => {
    setActiveNav("router");
    setUrlParam("nav", "router");
  });
}
