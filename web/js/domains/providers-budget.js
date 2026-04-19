export function pickFlaggedModels(providerModelState, limit = 20) {
  return Object.entries(providerModelState || {})
    .filter(([, row]) => row?.disabled_until_manual_review || row?.exclude_from_free_rotation)
    .slice(0, limit)
    .map(([key, row]) => ({
      key,
      disabled_until_manual_review: !!row?.disabled_until_manual_review,
      exclude_from_free_rotation: !!row?.exclude_from_free_rotation,
      failure_count: row?.failure_count || 0,
      last_error_type: row?.last_error_type || null,
      cooldown_until: row?.cooldown_until || 0,
    }));
}

export function updateBudgetBannerFromSnapshot(budget, setBudgetBanner) {
  const b = budget?.budget || {};
  const dayPct = b.daily_limit_usd > 0 ? (100.0 * (b.day_total_usd || 0) / b.daily_limit_usd) : 0;
  const monthPct = b.monthly_limit_usd > 0 ? (100.0 * (b.month_total_usd || 0) / b.monthly_limit_usd) : 0;
  const dayText = `${(b.day_total_usd || 0).toFixed(4)}/${(b.daily_limit_usd || 0).toFixed(2)} USD`;
  const monthText = `${(b.month_total_usd || 0).toFixed(4)}/${(b.monthly_limit_usd || 0).toFixed(2)} USD`;

  if (budget?.daily_exceeded || budget?.monthly_exceeded) {
    setBudgetBanner(
      `Budget exceeded. Day ${dayText} (${dayPct.toFixed(1)}%). Month ${monthText} (${monthPct.toFixed(1)}%).`,
      "bad"
    );
    return;
  }

  if (budget?.daily_warn || budget?.monthly_warn) {
    setBudgetBanner(
      `Budget warning near threshold. Day ${dayText} (${dayPct.toFixed(1)}%). Month ${monthText} (${monthPct.toFixed(1)}%).`,
      "warn"
    );
    return;
  }

  setBudgetBanner("");
}
