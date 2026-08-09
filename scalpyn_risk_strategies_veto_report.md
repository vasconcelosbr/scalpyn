
# Risk and Strategies Veto Report

`global_risk.validate_recommendation` and `strategies.validate_recommendation` are read-only typed tools. Candidate/shadow output validation requires both evidence records; `VETO` and `INVARIANT_CONFLICT` stop candidate creation in code. The staging systemic run persisted both validator calls [query: staging canary], and every regenerative candidate passed the deterministic guard before version creation. No Risk, Strategies, TP/SL, sizing, Spot exit or live pointer was mutated.
