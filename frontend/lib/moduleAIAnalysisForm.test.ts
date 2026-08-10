import assert from "node:assert/strict";
import test from "node:test";

import {
  getModuleAnalysisFormIssues,
  MODEL_COST_APPROVAL_PHRASE,
  type ModuleAnalysisFormValues,
} from "./moduleAIAnalysisForm";

const validForm: ModuleAnalysisFormValues = {
  question: "Quais são as causas do desvio observado?",
  model: "catalog-model",
  maxCostUsd: "0.02",
  inputCostPerMillion: "1",
  outputCostPerMillion: "5",
  maxInputTokens: "1000",
  maxOutputTokens: "500",
  requestTokenLimit: "1500",
  dailyTokenLimit: "3000",
  monthlyTokenLimit: "3000",
  pricingSourceUrl: "https://provider.example/pricing",
  approvalPhrase: MODEL_COST_APPROVAL_PHRASE,
};

test("accepts a complete, bounded approval form", () => {
  assert.deepEqual(getModuleAnalysisFormIssues(validForm), []);
});

test("treats placeholders and empty fields as missing values", () => {
  const issues = getModuleAnalysisFormIssues({
    ...validForm,
    question: "",
    maxCostUsd: "",
    pricingSourceUrl: "",
    approvalPhrase: "",
  });

  assert.ok(issues.includes("Descreva o objetivo da análise."));
  assert.ok(issues.includes("Informe um custo máximo maior que zero."));
  assert.ok(issues.includes("Informe a URL HTTPS da fonte oficial do preço."));
  assert.ok(issues.includes(`Digite exatamente: ${MODEL_COST_APPROVAL_PHRASE}`));
});

test("rejects inconsistent request, daily, and monthly token limits", () => {
  const issues = getModuleAnalysisFormIssues({
    ...validForm,
    requestTokenLimit: "1499",
    dailyTokenLimit: "1498",
    monthlyTokenLimit: "1497",
  });

  assert.ok(issues.includes("O limite da solicitação deve cobrir entrada mais saída."));
  assert.ok(issues.includes("O limite diário deve ser igual ou maior que o limite da solicitação."));
  assert.ok(issues.includes("O limite mensal deve ser igual ou maior que o limite diário."));
});
