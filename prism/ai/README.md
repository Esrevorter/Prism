# ai/ — on-device models (spec §8)

Fraud detector (≤5MB int8), transaction simulator, UX optimizer, FL client
with DP noise + secure aggregation. Hard rule: model inference never emits a
signing decision; outputs are advisory flags consumed by wallet/intent UI.
