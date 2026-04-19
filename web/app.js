import { wireOperationsDomain, refreshAll } from "./js/domains/operations.js";
import { wireEvaluationDomain } from "./js/domains/evaluation.js";
import { wireRouterDomain } from "./js/domains/router.js";
import { wireShellDomain } from "./js/domains/shell.js";
import { wireGovernanceDomain } from "./js/domains/governance.js";

function wireApp() {
  wireShellDomain();
  wireOperationsDomain();
  wireEvaluationDomain();
  wireRouterDomain();
  wireGovernanceDomain();
}

wireApp();
refreshAll();
