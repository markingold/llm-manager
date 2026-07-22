import { wireOperationsDomain, refreshAll } from "./js/domains/operations.js?v=20260722_1";
import { wireEvaluationDomain } from "./js/domains/evaluation.js?v=20260722_1";
import { wireRouterDomain } from "./js/domains/router.js?v=20260722_1";
import { wireShellDomain } from "./js/domains/shell.js?v=20260722_1";
import { wireGovernanceDomain } from "./js/domains/governance.js?v=20260722_1";

function wireApp() {
  wireShellDomain();
  wireOperationsDomain();
  wireEvaluationDomain();
  wireRouterDomain();
  wireGovernanceDomain();
}

wireApp();
refreshAll();
