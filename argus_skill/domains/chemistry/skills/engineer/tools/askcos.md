---
name: ASKCOS Retrosynthesis
description: Integrate an authorized ASKCOS deployment for reaction prediction or retrosynthesis without assuming public hosted access.
category: chemistry-tool-askcos
version: 1
---

ASKCOS is a multi-service deployment, not a token-free local function. Start
from <https://gitlab.com/mlpds_mit/askcosv2/askcos2_core>, verify repository
versions, model weights, licenses, and deployment requirements, and use only an
endpoint the project is authorized to access.

Probe health plus one documented request. Retain deployment commit, model and
stock versions, request/response JSON, target identity, search limits, timing,
and service logs.

Never silently substitute a mock endpoint. A proposed route remains predictive
evidence and requires separate feasibility or experimental validation.
