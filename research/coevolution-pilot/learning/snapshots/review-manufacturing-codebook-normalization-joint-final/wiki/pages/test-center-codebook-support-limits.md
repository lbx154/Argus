---
title: Test-center codebook support limits
description: Structural ambiguities that affect normalization of the supplied test-center reason logs.
---

The product codebooks in `/app/data/codebook_P1_POWER.csv`, `/app/data/codebook_P2_CTRL.csv`, and `/app/data/codebook_P3_RF.csv` contain groups of different codes with identical standard labels, keywords, and station scopes. Text and station metadata cannot distinguish codes within such a group. A normalization procedure should therefore use a documented deterministic representative and lower its confidence rather than claim an evidence-based code distinction.

Several classes are component-specific even when log phrases are not. In particular, missing-component, reversed-component, and cold-solder labels often name a component, while records in `/app/data/test_center_logs.csv` may state only the defect class or a signal name. Such text supports a code only when the product codebook yields one distinct label after entity association; otherwise `UNKNOWN` is the appropriate engineering alert.

These limits were observed directly in the source codebooks during the bounded inspection summarized by observation `call_9Gqj7VK8pMpNHtfMeKSgr61j`.
