export const MODEL_COST_APPROVAL_PHRASE = "APROVO MODELO E CUSTO";

export type ModuleAnalysisFormValues = {
  question: string;
  model: string;
  maxCostUsd: string;
  inputCostPerMillion: string;
  outputCostPerMillion: string;
  maxInputTokens: string;
  maxOutputTokens: string;
  requestTokenLimit: string;
  dailyTokenLimit: string;
  monthlyTokenLimit: string;
  pricingSourceUrl: string;
  approvalPhrase: string;
};

function positiveDecimal(value: string) {
  const parsed = Number(value);
  return value.trim() !== "" && Number.isFinite(parsed) && parsed > 0;
}

function nonNegativeDecimal(value: string) {
  const parsed = Number(value);
  return value.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;
}

function positiveInteger(value: string) {
  const parsed = Number(value);
  return value.trim() !== "" && Number.isInteger(parsed) && parsed > 0;
}

export function getModuleAnalysisFormIssues(values: ModuleAnalysisFormValues) {
  const issues: string[] = [];
  const maxInput = Number(values.maxInputTokens);
  const maxOutput = Number(values.maxOutputTokens);
  const requestLimit = Number(values.requestTokenLimit);
  const dailyLimit = Number(values.dailyTokenLimit);
  const monthlyLimit = Number(values.monthlyTokenLimit);

  if (!values.question.trim()) issues.push("Descreva o objetivo da análise.");
  if (!values.model.trim()) issues.push("Selecione um modelo válido do catálogo.");
  if (!positiveDecimal(values.maxCostUsd)) issues.push("Informe um custo máximo maior que zero.");
  if (!nonNegativeDecimal(values.inputCostPerMillion)) issues.push("Informe o preço de entrada por milhão de tokens.");
  if (!nonNegativeDecimal(values.outputCostPerMillion)) issues.push("Informe o preço de saída por milhão de tokens.");
  if (!positiveInteger(values.maxInputTokens)) issues.push("Informe o máximo de tokens de entrada.");
  if (!positiveInteger(values.maxOutputTokens)) issues.push("Informe o máximo de tokens de saída.");
  if (!positiveInteger(values.requestTokenLimit)) {
    issues.push("Informe o limite de tokens desta solicitação.");
  } else if (
    positiveInteger(values.maxInputTokens)
    && positiveInteger(values.maxOutputTokens)
    && requestLimit < maxInput + maxOutput
  ) {
    issues.push("O limite da solicitação deve cobrir entrada mais saída.");
  }
  if (!positiveInteger(values.dailyTokenLimit)) {
    issues.push("Informe o limite diário de tokens.");
  } else if (positiveInteger(values.requestTokenLimit) && dailyLimit < requestLimit) {
    issues.push("O limite diário deve ser igual ou maior que o limite da solicitação.");
  }
  if (!positiveInteger(values.monthlyTokenLimit)) {
    issues.push("Informe o limite mensal de tokens.");
  } else if (positiveInteger(values.dailyTokenLimit) && monthlyLimit < dailyLimit) {
    issues.push("O limite mensal deve ser igual ou maior que o limite diário.");
  }
  if (!values.pricingSourceUrl.trim().startsWith("https://")) {
    issues.push("Informe a URL HTTPS da fonte oficial do preço.");
  }
  if (values.approvalPhrase.trim() !== MODEL_COST_APPROVAL_PHRASE) {
    issues.push(`Digite exatamente: ${MODEL_COST_APPROVAL_PHRASE}`);
  }

  return issues;
}
